local util = require("calc.util")

local M = {}

function M.classify(n)
  if n < 0 then
    return "negative"
  elseif n == 0 then
    return "zero"
  end
  return "positive"
end

function M.total(xs)
  local sum = 0
  for _, x in ipairs(xs) do
    sum = sum + util.clamp(x, 0, 100)
  end
  return sum
end

return M
