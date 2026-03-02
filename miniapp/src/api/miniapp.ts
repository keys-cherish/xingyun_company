import { apiClient } from './client';
import type { MiniAppAuthResponse, MiniAppPreload } from '../types';

export const authMiniApp = async (initData: string, companyId?: number) => {
  const payload: Record<string, string | number> = { init_data: initData };
  if (companyId) {
    payload.company_id = companyId;
  }
  return apiClient
    .post('api/miniapp/auth', { json: payload })
    .json<MiniAppAuthResponse>();
};

export const fetchPreload = async (companyId?: number) => {
  const query = companyId ? `?company_id=${companyId}` : '';
  return apiClient.get(`api/miniapp/preload${query}`).json<MiniAppPreload>();
};
