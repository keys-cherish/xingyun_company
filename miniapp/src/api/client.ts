import ky from 'ky';

const baseUrl = import.meta.env.VITE_API_BASE_URL || '';
let sessionToken = '';

export const setSessionToken = (token: string) => {
  sessionToken = token;
};

export const apiClient = ky.create({
  prefixUrl: baseUrl.replace(/\/$/, ''),
  timeout: 8000,
  hooks: {
    beforeRequest: [
      (request) => {
        if (sessionToken) {
          request.headers.set('Authorization', `Bearer ${sessionToken}`);
        }
      },
    ],
  },
});
