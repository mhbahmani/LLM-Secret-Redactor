import plugin from "./index.mjs";

export default {
  id: "secret-redactor",
  async server(context) {
    return plugin(context);
  },
};
