import { z } from 'zod';

export const garminConnectLoginSchema = z.object({
  email: z
    .string()
    .min(1, 'Email is required')
    .email('Please enter a valid email address'),
  password: z.string().min(1, 'Password is required'),
});

export const garminConnectMFASchema = z.object({
  mfa_code: z
    .string()
    .min(1, 'MFA code is required')
    .regex(/^\d{6}$/, 'MFA code must be 6 digits'),
});

export type GarminConnectLoginFormData = z.infer<
  typeof garminConnectLoginSchema
>;
export type GarminConnectMFAFormData = z.infer<typeof garminConnectMFASchema>;
