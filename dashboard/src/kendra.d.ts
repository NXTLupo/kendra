export {};

declare global {
  interface Window {
    kendra?: {
      request<T = unknown>(method: string, params?: Record<string, unknown>): Promise<T>;
      platform: string;
    };
  }
}
