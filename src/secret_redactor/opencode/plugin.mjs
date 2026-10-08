import { createHooks } from "./hooks.mjs";

export default {
  id: "secret-redactor",
  async server() {
    return createHooks();
  },
};
