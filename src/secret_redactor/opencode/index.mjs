import { createV1Hooks } from "./v1.mjs";
import { registerV2Hooks } from "./v2.mjs";

async function plugin(input, options) {
  if (input && (input.session?.hook || input.tool?.hook)) {
    registerV2Hooks(input);
  }

  return createV1Hooks();
}

export default plugin;
