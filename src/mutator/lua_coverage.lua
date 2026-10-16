-- Loaded before busted.runner, so subscriptions outlive every insulated file.
return function(destination)
  local runner = require('luacov.runner')
  local stats = require('luacov.stats')
  local core = require('busted.core')
  local json = require('dkjson')
  local object = {__jsontype = 'object'}
  local previous, aggregate = {}, {}
  local data_identity, file_identities = runner.data, {}
  local memberships, outside, specs = setmetatable({}, object), setmetatable({}, object), setmetatable({}, object)
  local active, started, busted
  local unsafe, closing = false, false
  local helper_source = debug.getinfo(1, 'S').source:sub(2):gsub('\\', '/')
  local file_included = runner.file_included
  runner.file_included = function(filename)
    return filename:gsub('\\', '/') ~= helper_source and file_included(filename)
  end

  local function snapshot()
    local current = {}
    if runner.initialized and not closing and debug.gethook() ~= runner.debug_hook then unsafe = true end
    if runner.data ~= data_identity then unsafe = true end
    data_identity = runner.data
    for filename, file in pairs(runner.data) do
      if file_identities[filename] and file_identities[filename] ~= file then unsafe = true end
      file_identities[filename] = file
      local counts = {}
      current[filename] = counts
      -- cluacov uses '/' on Windows while the reporter's untested-file
      -- discovery uses native separators. Merge aliases before reporting.
      local aggregate_name = filename:gsub('^%.[/\\]', ''):gsub('[/\\]', package.config:sub(1, 1))
      aggregate[aggregate_name] = aggregate[aggregate_name] or {max = 0, max_hits = 0}
      local total = aggregate[aggregate_name]
      local before = previous[filename] or {}
      for line, hits in pairs(file) do
        if type(line) == 'number' and hits > 0 then
          counts[line] = hits
          local delta = hits - (before[line] or 0)
          if delta < 0 then unsafe = true; delta = hits end
          if delta > 0 then
            total[line] = (total[line] or 0) + delta
            total.max = math.max(total.max, line)
            total.max_hits = math.max(total.max_hits, total[line])
            if active then
              memberships[filename] = memberships[filename] or {}
              local lines = memberships[filename]
              local key = tostring(line)
              lines[key] = lines[key] or {}
              lines[key][active] = true
            else
              outside[filename] = outside[filename] or {}
              outside[filename][tostring(line)] = true
            end
          end
        end
      end
      for line in pairs(before) do
        if not counts[line] then unsafe = true end
      end
    end
    for filename in pairs(previous) do
      if not current[filename] then unsafe = true end
    end
    previous = current
  end

  -- Keep both the top-level data table and per-file tables intact: the
  -- optional native hook may cache them. Explicit/tick saves publish the
  -- cumulative aggregate without resetting counters.
  runner.save_stats = function()
    snapshot()
    stats.save(runner.configuration.statsfile, aggregate)
  end

  package.loaded['busted.core'] = function(...)
    assert(not busted, 'coverage collector supports one Busted core')
    busted = core(...)
    busted.subscribe({'file', 'start'}, function(file)
      snapshot()
      if active then unsafe = true end
      active = file.name
      started = busted.monotime()
      return nil, true
    end)
    busted.subscribe({'file', 'end'}, function(file)
      snapshot()
      if active ~= file.name or not started then unsafe = true end
      local elapsed = started and math.max(0, busted.monotime() - started) or 0
      specs[file.name] = (specs[file.name] or 0) + elapsed
      active, started = nil, nil
      return nil, true
    end)
    return busted
  end

  -- Called only after busted.runner returns successfully, after all exit
  -- subscribers. Their source execution belongs to the full-suite fallback.
  return function()
    assert(busted and runner.initialized, 'Busted coverage never started')
    snapshot()
    if active then unsafe = true end
    closing = true
    runner.shutdown()
    stats.save(runner.configuration.statsfile, aggregate)
    local publication = {
      version = 1, complete = true, unsafe = unsafe,
      specs = specs, lines = memberships, fallback = outside,
    }
    local encoded = assert(json.encode(publication))
    local output = assert(io.open(destination, 'wb'))
    assert(output:write(encoded))
    assert(output:close())
  end
end
