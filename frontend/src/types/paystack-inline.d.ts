// The package ships no types. Only what Payment.tsx uses, from its README.
declare module "@paystack/inline-js" {
  type Callbacks = {
    onSuccess?: (transaction: { id: number; reference: string; message: string }) => void;
    onCancel?: () => void;
    onError?: (error: { message: string }) => void;
    onLoad?: (transaction: { id: number; accessCode: string }) => void;
  };

  export default class PaystackPop {
    resumeTransaction(accessCode: string, callbacks?: Callbacks): void;
  }
}
