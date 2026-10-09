import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { registerV2Hooks } = require("./v2.js");

export default {
  id: "secret-redactor",
  async server(context) {
    registerV2Hooks(context, true);
  },
};
