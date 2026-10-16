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
            lines[key] = lines[key] or {}
            lines[key][active] = true
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
    for _, lines in pairs(memberships) do
      for line, members in pairs(lines) do
        local ids = {}
        for id in pairs(members) do ids[#ids + 1] = id end
        table.sort(ids)
        lines[line] = ids
      end
    end
    local publication = {
      version = 2, complete = true, unsafe = unsafe,
      specIds = spec_ids, specs = specs, lines = memberships, fallback = outside,
    }
    local encoded = assert(json.encode(publication))
    local output = assert(io.open(destination, 'wb'))
    assert(output:write(encoded))
    assert(output:close())
  end
end
