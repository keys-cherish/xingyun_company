import { defineStore } from 'pinia';

import { setSessionToken } from '../api/client';
import { authMiniApp, fetchPreload } from '../api/miniapp';
import type { MiniAppPreload } from '../types';
import { getTelegramInitData } from '../utils/telegram';

interface BootstrapState {
  loading: boolean;
  refreshing: boolean;
  error: string;
  preload: MiniAppPreload | null;
  tokenReady: boolean;
}

export const useBootstrapStore = defineStore('bootstrap', {
  state: (): BootstrapState => ({
    loading: true,
    refreshing: false,
    error: '',
    preload: null,
    tokenReady: false,
  }),
  actions: {
    async bootstrap(companyId?: number) {
      this.loading = true;
      this.error = '';
      try {
        const initData = getTelegramInitData();
        if (!initData) {
          throw new Error('Missing Telegram initData. Open this page from Telegram Mini App.');
        }

        const auth = await authMiniApp(initData, companyId);
        setSessionToken(auth.session_token);
        this.tokenReady = true;
        this.preload = auth.preload;
      } catch (err) {
        this.error = err instanceof Error ? err.message : 'Bootstrap failed';
      } finally {
        this.loading = false;
      }
    },

    async refresh(companyId?: number) {
      if (!this.tokenReady) {
        await this.bootstrap(companyId);
        return;
      }

      this.refreshing = true;
      this.error = '';
      try {
        this.preload = await fetchPreload(companyId);
      } catch (err) {
        this.error = err instanceof Error ? err.message : 'Refresh failed';
      } finally {
        this.refreshing = false;
      }
    },
  },
});
