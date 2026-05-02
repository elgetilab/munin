import '@testing-library/jest-dom/vitest';
import { server } from './msw-server';
import { afterAll, afterEach, beforeAll } from 'vitest';

// jsdom doesn't implement scrollIntoView
Element.prototype.scrollIntoView = () => {};

beforeAll(() => server.listen({ onUnhandledRequest: 'warn' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
