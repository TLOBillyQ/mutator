local calc = require("calc")

describe("calc", function()
  it("classifies", function()
    assert.equal("negative", calc.classify(-1))
    assert.equal("positive", calc.classify(1))
  end)

  it("totals clamped values", function()
    assert.equal(105, calc.total({ 5, 200 }))
  end)
end)
