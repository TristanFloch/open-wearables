import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { Check, Loader2 } from 'lucide-react';
import { motion } from 'motion/react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Button } from '@/components/ui/button';
import {
  useGarminConnectLogin,
  useGarminConnectMFA,
} from '@/hooks/api/use-garmin-connect';
import {
  garminConnectLoginSchema,
  garminConnectMFASchema,
  type GarminConnectLoginFormData,
  type GarminConnectMFAFormData,
} from '@/lib/validation/garmin-connect.schemas';
import { queryClient } from '@/lib/query/client';
import { queryKeys } from '@/lib/query/keys';

type DialogStep = 'credentials' | 'mfa' | 'success';

interface GarminConnectDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  userId: string;
  onSuccess?: () => void;
}

export function GarminConnectDialog({
  open,
  onOpenChange,
  userId,
  onSuccess,
}: GarminConnectDialogProps) {
  const [step, setStep] = useState<DialogStep>('credentials');
  const [sessionId, setSessionId] = useState<string | null>(null);

  const loginMutation = useGarminConnectLogin(userId);
  const mfaMutation = useGarminConnectMFA(userId);

  const loginForm = useForm<GarminConnectLoginFormData>({
    resolver: zodResolver(garminConnectLoginSchema),
    defaultValues: { email: '', password: '' },
  });

  const mfaForm = useForm<GarminConnectMFAFormData>({
    resolver: zodResolver(garminConnectMFASchema),
    defaultValues: { mfa_code: '' },
  });

  const handleLogin = (data: GarminConnectLoginFormData) => {
    loginMutation.mutate(data, {
      onSuccess: (response) => {
        if (response.requires_mfa && response.session_id) {
          setSessionId(response.session_id);
          setStep('mfa');
        } else if (response.success) {
          queryClient.invalidateQueries({
            queryKey: queryKeys.connections.all(userId),
          });
          setStep('success');
          onSuccess?.();
        }
      },
    });
  };

  const handleMFA = (data: GarminConnectMFAFormData) => {
    if (!sessionId) return;
    mfaMutation.mutate(
      { session_id: sessionId, mfa_code: data.mfa_code },
      {
        onSuccess: () => {
          setStep('success');
          onSuccess?.();
        },
      }
    );
  };

  const handleClose = (isOpen: boolean) => {
    if (!isOpen) {
      // Reset state when dialog closes
      setStep('credentials');
      setSessionId(null);
      loginForm.reset();
      mfaForm.reset();
      loginMutation.reset();
      mfaMutation.reset();
    }
    onOpenChange(isOpen);
  };

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent className="max-w-md">
        {step === 'credentials' && (
          <>
            <DialogHeader>
              <DialogTitle>Connect Garmin Connect</DialogTitle>
              <DialogDescription>
                Sign in with your personal Garmin account. Your credentials are
                sent directly to Garmin and are not stored.
              </DialogDescription>
            </DialogHeader>
            <form
              onSubmit={loginForm.handleSubmit(handleLogin)}
              className="space-y-4"
            >
              <div className="space-y-2">
                <Label htmlFor="gc-email" className="text-zinc-300">
                  Email
                </Label>
                <Input
                  id="gc-email"
                  type="email"
                  autoComplete="email"
                  placeholder="you@example.com"
                  className="bg-zinc-800 border-zinc-700"
                  {...loginForm.register('email')}
                />
                {loginForm.formState.errors.email && (
                  <p className="text-xs text-red-500">
                    {loginForm.formState.errors.email.message}
                  </p>
                )}
              </div>
              <div className="space-y-2">
                <Label htmlFor="gc-password" className="text-zinc-300">
                  Password
                </Label>
                <Input
                  id="gc-password"
                  type="password"
                  autoComplete="current-password"
                  placeholder="Your Garmin password"
                  className="bg-zinc-800 border-zinc-700"
                  {...loginForm.register('password')}
                />
                {loginForm.formState.errors.password && (
                  <p className="text-xs text-red-500">
                    {loginForm.formState.errors.password.message}
                  </p>
                )}
              </div>
              <DialogFooter className="gap-3">
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => handleClose(false)}
                >
                  Cancel
                </Button>
                <Button type="submit" disabled={loginMutation.isPending}>
                  {loginMutation.isPending ? (
                    <>
                      <Loader2 className="h-4 w-4 animate-spin" />
                      Connecting...
                    </>
                  ) : (
                    'Sign In'
                  )}
                </Button>
              </DialogFooter>
            </form>
          </>
        )}

        {step === 'mfa' && (
          <>
            <DialogHeader>
              <DialogTitle>MFA Verification</DialogTitle>
              <DialogDescription>
                Enter the 6-digit code from your authenticator app or SMS.
              </DialogDescription>
            </DialogHeader>
            <form
              onSubmit={mfaForm.handleSubmit(handleMFA)}
              className="space-y-4"
            >
              <div className="space-y-2">
                <Label htmlFor="gc-mfa" className="text-zinc-300">
                  MFA Code
                </Label>
                <Input
                  id="gc-mfa"
                  type="text"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  placeholder="000000"
                  maxLength={6}
                  className="bg-zinc-800 border-zinc-700 text-center text-lg tracking-widest"
                  {...mfaForm.register('mfa_code')}
                />
                {mfaForm.formState.errors.mfa_code && (
                  <p className="text-xs text-red-500">
                    {mfaForm.formState.errors.mfa_code.message}
                  </p>
                )}
              </div>
              <DialogFooter className="gap-3">
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => {
                    setStep('credentials');
                    setSessionId(null);
                    mfaForm.reset();
                    mfaMutation.reset();
                  }}
                >
                  Back
                </Button>
                <Button type="submit" disabled={mfaMutation.isPending}>
                  {mfaMutation.isPending ? (
                    <>
                      <Loader2 className="h-4 w-4 animate-spin" />
                      Verifying...
                    </>
                  ) : (
                    'Verify'
                  )}
                </Button>
              </DialogFooter>
            </form>
          </>
        )}

        {step === 'success' && (
          <div className="py-8 text-center">
            <motion.div
              initial={{ scale: 0 }}
              animate={{ scale: 1 }}
              transition={{
                type: 'spring',
                stiffness: 200,
                damping: 12,
              }}
              className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-full bg-green-500/20 shadow-[0_0_30px_hsla(145,100%,50%,0.3)]"
            >
              <Check className="h-8 w-8 text-green-500" />
            </motion.div>
            <h2 className="mb-2 text-xl font-medium text-white">Connected</h2>
            <p className="mb-6 text-sm text-zinc-400">
              Your Garmin data will start syncing shortly.
            </p>
            <Button variant="outline" onClick={() => handleClose(false)}>
              Close
            </Button>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
