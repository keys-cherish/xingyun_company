export const getTelegramInitData = (): string => {
  const webApp = window.Telegram?.WebApp;
  if (webApp) {
    webApp.ready();
    webApp.expand();
    return webApp.initData || '';
  }
  return String(import.meta.env.VITE_DEV_INIT_DATA || '');
};
