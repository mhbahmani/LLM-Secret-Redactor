const { createV1Hooks } = require("./v1.js");
const { registerV2Hooks } = require("./v2.js");

async function plugin(input, options) {
  if (input && (input.session?.hook || input.tool?.hook)) {
    registerV2Hooks(input);
  }

  return createV1Hooks();
}

module.exports = plugin;
