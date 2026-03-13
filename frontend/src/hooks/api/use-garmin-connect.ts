import { useMutation } from '@tanstack/react-query';
import { garminConnectService } from '@/lib/api/services/garmin-connect.service';
import { queryClient } from '@/lib/query/client';
import { queryKeys } from '@/lib/query/keys';
import { toast } from 'sonner';

export function useGarminConnectLogin(userId: string) {
  return useMutation({
    mutationFn: (data: { email: string; password: string }) =>
      garminConnectService.login({ ...data, user_id: userId }),
    // No toast on success — caller handles MFA vs direct success branching
    onError: (error: unknown) => {
      const message =
        error instanceof Error
          ? error.message
          : 'Login failed. Check your credentials.';
      toast.error(message);
    },
  });
}

export function useGarminConnectMFA(userId: string) {
  return useMutation({
    mutationFn: (data: { session_id: string; mfa_code: string }) =>
      garminConnectService.completeMFA(data, userId),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: queryKeys.connections.all(userId),
      });
      toast.success('Successfully connected to Garmin Connect');
    },
    onError: (error: unknown) => {
      const message =
        error instanceof Error
          ? error.message
          : 'MFA verification failed. Check your code.';
      toast.error(message);
    },
  });
}

