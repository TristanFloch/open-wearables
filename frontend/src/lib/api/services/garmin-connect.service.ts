import { apiClient } from '../client';
import { API_ENDPOINTS } from '../config';
import type { GarminConnectLoginResponse } from '../types';

export const garminConnectService = {
  async login(data: {
    email: string;
    password: string;
    user_id: string;
  }): Promise<GarminConnectLoginResponse> {
    return apiClient.post<GarminConnectLoginResponse>(
      API_ENDPOINTS.garminConnectLogin,
      data
    );
  },

  async completeMFA(
    data: { session_id: string; mfa_code: string },
    userId: string
  ): Promise<GarminConnectLoginResponse> {
    const params = new URLSearchParams({ user_id: userId });
    return apiClient.post<GarminConnectLoginResponse>(
      `${API_ENDPOINTS.garminConnectMFA}?${params}`,
      data
    );
  },

};
