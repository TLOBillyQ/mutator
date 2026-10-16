"""Sequential baseline/candidate collector profiling, without mutation workers.

Run only when other memory-heavy benchmark runs have finished. Reports sampled
Lua heap and OS peak working set/RSS separately; neither is serialized size.
"""
import argparse
import ctypes
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from mutator.crapper_link import ensure_crapper
from mutator.lua_coverage import _literal, _plan


def rss(process):
    if os.name == 'nt':
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in (
                    'PeakWorkingSetSize', 'WorkingSetSize', 'QuotaPeakPagedPoolUsage',
                    'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
                    'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage')]

        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        function = ctypes.windll.psapi.GetProcessMemoryInfo
        function.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        if not function(process._handle, ctypes.byref(counters), counters.cb):
            raise ctypes.WinError()
        return counters.PeakWorkingSetSize
    if sys.platform != 'linux':
        raise RuntimeError(f'RSS profiling unsupported on {sys.platform}')
    try:
        rows = Path(f'/proc/{process.pid}/status').read_text().splitlines()
    except FileNotFoundError:
        # The process can exit between samples. Linux results are explicitly
        # reported as sampled lower bounds rather than lifetime peaks.
        return 0
    values = [int(row.split()[1]) * 1024 for row in rows if row.startswith(('VmRSS:', 'VmHWM:'))]
    return max(values, default=0)


def run(root, source, collector, directory, label):
    if os.name != 'nt' and sys.platform != 'linux':
        raise RuntimeError(f'RSS profiling unsupported on {sys.platform}')
    runners = ensure_crapper().runners
    command, cwd, _report = _plan(root, source, None)
    stats = directory / f'{label}.stats'
    config = directory / f'{label}.cfg.lua'
    publication = directory / f'{label}.json'
    heap = directory / f'{label}.heap'
    helper = directory / f'{label}.lua'
    config.write_text(runners.lua_coverage_config(stats, directory / f'{label}.lcov'), encoding='utf-8')
    # Sample at segment boundaries, JSON encoding and writes in both collectors.
    # No forced GC: include the normal collector's transient allocations.
    prefix = """local heap_peak = 0
local function sample_heap()
  heap_peak = math.max(heap_peak, collectgarbage('count'))
end
"""
    instrumented = collector.replace('local function snapshot()', 'local function snapshot()\n    sample_heap()')
    instrumented = instrumented.replace('if not ok then error(err, 0) end', 'sample_heap()\n    if not ok then error(err, 0) end')
    instrumented = instrumented.replace("local json = require('dkjson')", """local original_json = require('dkjson')
  local json = {encode = function(...)
    local value, err = original_json.encode(...)
    sample_heap()
    return value, err
  end}
  local io = setmetatable({open = function(...)
    local file, err = _G.io.open(...)
    if not file then return nil, err end
    return {
      write = function(_, value) sample_heap(); return file:write(value) end,
      close = function() sample_heap(); return file:close() end,
    }
  end}, {__index = _G.io})""")
    instrumented = instrumented.replace('assert(output:close())',
        "assert(output:close()); local heap_output = assert(_G.io.open(" + _literal(heap.as_posix())
        + ", 'w')); heap_output:write(tostring(heap_peak)); heap_output:close()")
    helper.write_text(prefix + instrumented, encoding='utf-8')
    command = list(command)
    entry = command.index('-e') + 1
    bootstrap = f'local finish = dofile({_literal(helper.as_posix())})({_literal(publication.as_posix())}); '
    command[entry] = command[entry].replace("require('busted.runner')", bootstrap + "require('busted.runner')", 1)
    assert command[entry].endswith('; os.exit(0)')
    command[entry] = command[entry][:-len('; os.exit(0)')] + '; finish(); os.exit(0)'
    command += ['-c', f'--coverage-config-file={config}']
    peak_rss = 0
    errors = []
    stop = threading.Event()
    started = time.monotonic()
    with (directory / f'{label}.log').open('wb') as log:
        process = subprocess.Popen(command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT)
        def monitor():
            nonlocal peak_rss
            try:
                while not stop.is_set():
                    peak_rss = max(peak_rss, rss(process))
                    stop.wait(0.02)
            except Exception as error:
                errors.append(str(error))
        thread = threading.Thread(target=monitor)
        thread.start()
        code = process.wait()
        stop.set()
        thread.join()
        if os.name == 'nt':
            # Popen retains its Windows process handle after wait(). Query the
            # lifetime high-water mark before the handle can be closed, covering
            # allocations after the final periodic sample.
            peak_rss = max(peak_rss, rss(process))
    if code or errors or peak_rss <= 0:
        raise RuntimeError(f'{label}: exit={code}, monitor={errors}, log={directory / (label + ".log")}')
    raw = json.loads(publication.read_text(encoding='utf-8'))
    result = {
        'elapsed_seconds': time.monotonic() - started,
        'peak_rss_bytes': peak_rss,
        'rss_measurement': ('Windows lifetime PeakWorkingSetSize queried after exit'
                            if os.name == 'nt' else 'Linux sampled VmHWM/RSS lower bound'),
        'sampled_lua_heap_peak_kib': float(heap.read_text()),
        'artifact_bytes': publication.stat().st_size,
        'specs': len(raw['specIds']),
        'collector_sha256': hashlib.sha256(collector.encode()).hexdigest(),
        'unsafe': raw['unsafe'],
    }
    print(label, json.dumps(result), flush=True)
    return raw, result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--source', default='src/app/cosmetics/transaction_context.lua')
    parser.add_argument('--baseline', default='adc4d34')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--require-native', action='store_true')
    args = parser.parse_args()
    root = args.root.resolve()
    checkout = Path(__file__).resolve().parents[2]
    baseline = subprocess.check_output(['git', 'show', f'{args.baseline}:src/mutator/lua_coverage.lua'], cwd=checkout).decode()
    candidate = (checkout / 'src/mutator/lua_coverage.lua').read_text(encoding='utf-8')
    def project_state():
        head = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD']).decode().strip()
        tracked = subprocess.check_output(['git', '-C', str(root), 'ls-files', '-z']).decode().split('\0')
        digest = hashlib.sha256()
        for name in sorted(filter(None, tracked)):
            digest.update(name.encode())
            digest.update(b'\0')
            path = root / name
            digest.update(b'file\0' + path.read_bytes() if path.is_file() else b'missing\0')
        diff = subprocess.check_output(['git', '-C', str(root), 'diff', 'HEAD', '--binary'])
        return {'head': head, 'tracked_content_sha256': digest.hexdigest(),
                'diff_sha256': hashlib.sha256(diff).hexdigest()}

    initial_state = project_state()
    source_hash = hashlib.sha256((root / args.source).read_bytes()).hexdigest()
    native_probe = None
    if args.require_native:
        runners = ensure_crapper().runners
        lua = runners.lua_interpreter()
        native_probe = subprocess.check_output(
            [lua, '-e', runners._lua_package_setup(lua) + "require('cluacov.hook'); print(require('cluacov.version'))"],
            cwd=root, text=True).strip()
    artifacts = checkout / 'target/lua-perf/collector-memory'
    artifacts.mkdir(parents=True, exist_ok=True)
    first, before = run(root, root / args.source, baseline, artifacts, 'baseline')
    between_state = project_state()
    second, after = run(root, root / args.source, candidate, artifacts, 'candidate')
    final_state = project_state()
    parity = {key: first[key] == second[key] for key in ('version', 'complete', 'unsafe', 'specIds', 'lines', 'fallback')}
    source_after = hashlib.sha256((root / args.source).read_bytes()).hexdigest()
    assert source_hash == source_after, 'Source bytes changed during benchmark'
    report = {'project_commit': initial_state['head'],
              'project_states': {'before': initial_state, 'between': between_state, 'after': final_state},
              'project_state_unchanged': initial_state == between_state == final_state,
              'tracked_inputs_unchanged': initial_state['tracked_content_sha256'] == between_state['tracked_content_sha256'] == final_state['tracked_content_sha256'],
              'source': args.source, 'source_sha256': source_hash, 'source_unchanged': True,
              'native_probe': native_probe,
              'baseline_commit': args.baseline, 'baseline': before, 'candidate': after, 'parity': parity,
              'rss_sampling_seconds': 0.02,
              'heap_sampling': 'segment boundaries, JSON encode returns, publication writes; no forced GC',
              'environment': {name: os.environ.get(name) for name in ('LUA_PATH', 'LUA_CPATH')}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    assert report['project_state_unchanged'], 'Project revision, diff or tracked content changed during benchmark'
    assert report['tracked_inputs_unchanged'], 'Tracked project inputs changed during benchmark'
    assert all(parity.values()), parity
    assert not first['unsafe'] and not second['unsafe']


if __name__ == '__main__':
    main()
