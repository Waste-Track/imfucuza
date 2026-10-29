import { useState } from "react";
import heroPhoto from "../assets/landing-hero.jpg";
import { Icon } from "../components/Icon";
import { Button } from "../components/ui";
import { cedis } from "../lib/format";
import { saveIntent, type Intent } from "../lib/intent";
import { SignIn } from "./SignIn";

// The flat fee shown before sign-in. The Engine's fee (GET /v1/me) is what's charged.
const FEE_PESEWAS = 1000;

/** The first page for anyone not signed in. */
export function Landing() {
  const [step, setStep] = useState<{ intent?: Intent } | null>(null);

  if (step) {
    return (
      <SignIn
        intent={step.intent}
        onBack={() => {
          saveIntent(undefined);
          setStep(null);
        }}
      />
    );
  }

  const start = (intent?: Intent) => {
    saveIntent(intent);
    setStep({ intent });
  };

  return (
    <div className="screen landing">
      <header className="landing-hero" style={{ backgroundImage: `url(${heroPhoto})` }}>
        <div className="eyebrow light">IMFUCUZA · MADINA, ACCRA</div>
        <h1>Waste collected at your door. Plastic that pays.</h1>
        <p>
          Request a tricycle pickup for a flat fee, or hand over sorted plastic and earn points for
          vouchers at shops near you.
        </p>
      </header>

      <main className="content">
        <button type="button" className="hero tap choice" onClick={() => start("household")}>
          <div>
            <div className="hero-title">Get my waste collected</div>
            <div className="hero-sub">Set up your household in two minutes</div>
          </div>
          <Icon name="forward" />
        </button>
        <button type="button" className="card tap choice" onClick={() => start("rider")}>
          <div>
            <strong className="big">Register as a rider</strong>
            <div className="muted">Get pickup jobs near you and earn per pickup</div>
          </div>
          <Icon name="forward" />
        </button>

        <section className="how" aria-labelledby="how-title">
          <h2 id="how-title" className="section-title flat">
            HOW IT WORKS
          </h2>
          <ol className="how-list">
            <li>
              <strong>Request</strong>
              <span>
                General waste for {cedis(FEE_PESEWAS)}, held on your mobile money, or sorted plastic
                for points.
              </span>
            </li>
            <li>
              <strong>A nearby rider comes</strong>
              <span>You'll see their name and can call them.</span>
            </li>
            <li>
              <strong>Give your PIN</strong>
              <span>
                You get a PIN by SMS. You're only charged once you give it to the rider after
                collection.
              </span>
            </li>
          </ol>
        </section>

        <div className="stack center">
          <p className="muted">Already registered?</p>
          <Button variant="secondary" onClick={() => start(undefined)}>
            Sign in
          </Button>
        </div>
      </main>
    </div>
  );
}
