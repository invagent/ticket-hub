// Shared MSW node server for vitest. Tests register handlers via
// `server.use(...)` inside individual test cases.

import { setupServer } from "msw/node";

export const server = setupServer();
