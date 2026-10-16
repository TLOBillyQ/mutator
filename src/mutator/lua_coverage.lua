-- Loaded before busted.runner, so subscriptions outlive every insulated file.
return function(destination)
  local runner = require('luacov.runner')
  local stats = require('luacov.stats')
  local core = require('busted.core')
  local json = require('dkjson')
  local object = {__jsontype = 'object'}
  local aggregate = {}
  local data_identity = runner.data
  local memberships, outside, specs = setmetatable({}, object), setmetatable({}, object), setmetatable({}, object)
  local spec_ids, spec_numbers = {}, {}
  local active, active_name, started, busted
  local unsafe, closing = false, false
  local helper_source = debug.getinfo(1, 'S').source:sub(2):gsub('\\', '/')
  local file_included = runner.file_included
  runner.file_included = function(filename)
    return filename:gsub('\\', '/') ~= helper_source and file_included(filename)
  end

  local function consume()
    if runner.data ~= data_identity then unsafe = true end
    local segment = runner.data
    runner.data = {}
    data_identity = runner.data
    -- Share immutable packed memberships across lines with the same history.
    -- This cache is segment-local: retaining every historical transition would
    -- keep obsolete memberships alive for the duration of the suite.
    local transitions = {}
    local byte_index = active and (active // 8 + 1)
    local bit = active and (1 << (active % 8))
    for filename, file in pairs(segment) do
      -- Match the reporter's native paths and merge aliases before saving.
      local aggregate_name = filename:gsub('^%.[/\\]', ''):gsub('[/\\]', package.config:sub(1, 1))
      aggregate[aggregate_name] = aggregate[aggregate_name] or {max = 0, max_hits = 0}
      local total = aggregate[aggregate_name]
      for line, hits in pairs(file) do
        if type(line) == 'number' and hits > 0 then
          total[line] = (total[line] or 0) + hits
          total.max = math.max(total.max, line)
          total.max_hits = math.max(total.max_hits, total[line])
          if active ~= nil then
            memberships[filename] = memberships[filename] or {}
            local lines = memberships[filename]
            local key = tostring(line)
            local members = lines[key] or ''
            local byte = members:byte(byte_index) or 0
            if byte & bit == 0 then
              local updated = transitions[members]
              if not updated then
                updated = members:sub(1, byte_index - 1)
                  .. string.rep('\0', math.max(0, byte_index - #members - 1))
                  .. string.char(byte | bit) .. members:sub(byte_index + 1)
                transitions[members] = updated
              end
              lines[key] = updated
            end
          else
            outside[filename] = outside[filename] or {}
            outside[filename][tostring(line)] = true
          end
        end
      end
    end
  end

  local function snapshot()
    local hook, mask, count = debug.gethook()
    if runner.initialized and not closing and hook ~= runner.debug_hook then unsafe = true end
    -- Collector bookkeeping executes no project code. Avoid a coverage hook
    -- event for every counter while preserving the exact caller hook.
    debug.sethook()
    local ok, err = xpcall(consume, debug.traceback)
    debug.sethook(hook, mask, count)
    if not ok then error(err, 0) end
  end

  runner.save_stats = function()
    snapshot()
    stats.save(runner.configuration.statsfile, aggregate)
  end

  package.loaded['busted.core'] = function(...)
    assert(not busted, 'coverage collector supports one Busted core')
    busted = core(...)
    busted.subscribe({'file', 'start'}, function(file)
      snapshot()
      if active ~= nil then unsafe = true end
      local id = spec_numbers[file.name]
      if id == nil then
        id = #spec_ids
        spec_numbers[file.name] = id
        spec_ids[#spec_ids + 1] = file.name
      end
      active, active_name = id, file.name
      started = busted.monotime()
      return nil, true
    end)
    busted.subscribe({'file', 'end'}, function(file)
      snapshot()
      if active_name ~= file.name or not started then unsafe = true end
      local elapsed = started and math.max(0, busted.monotime() - started) or 0
      specs[file.name] = (specs[file.name] or 0) + elapsed
      active, active_name, started = nil, nil, nil
      return nil, true
    end)
    return busted
  end

  -- Successful return follows every exit subscriber; late source execution
  -- belongs to the full-suite fallback.
  return function()
    assert(busted and runner.initialized, 'Busted coverage never started')
    snapshot()
    if active ~= nil then unsafe = true end
    closing = true
    runner.shutdown()
    stats.save(runner.configuration.statsfile, aggregate)
    -- Stream the large line map rather than expanding all bitsets into Lua
    -- arrays and retaining the entire encoded document alongside them.
    local output = assert(io.open(destination, 'wb'))
    local function write(value) assert(output:write(value)) end
    write('{"version":2,"complete":true,"unsafe":' .. tostring(unsafe))
    write(',"specIds":' .. assert(json.encode(spec_ids)))
    write(',"specs":' .. assert(json.encode(specs)))
    write(',"fallback":' .. assert(json.encode(outside)) .. ',"lines":{')
    local file_separator = ''
    for filename, lines in pairs(memberships) do
      write(file_separator .. assert(json.encode(filename)) .. ':{')
      file_separator = ','
      local line_separator = ''
      local encoded_members = {}
      for line, members in pairs(lines) do
        local encoded = encoded_members[members]
        if not encoded then
          local ids = {}
          for byte_index = 1, #members do
            local byte = members:byte(byte_index)
            for bit_index = 0, 7 do
              if byte & (1 << bit_index) ~= 0 then
                ids[#ids + 1] = (byte_index - 1) * 8 + bit_index
              end
            end
          end
          encoded = assert(json.encode(ids))
          encoded_members[members] = encoded
        end
        write(line_separator .. assert(json.encode(line)) .. ':' .. encoded)
        line_separator = ','
      end
      write('}')
    end
    write('}}')
    assert(output:close())
  end
end
