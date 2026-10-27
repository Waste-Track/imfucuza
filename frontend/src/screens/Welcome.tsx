import { useState } from "react";
import { Icon } from "../components/Icon";
import { Button, Screen } from "../components/ui";
import { useSession } from "../session";
import { Register } from "./household/Register";
import { RiderRegister } from "./rider/RiderRegister";

/** A phone the Engine doesn't know yet: set up a household or apply as a rider. */
export function Welcome() {
  const { signOut } = useSession();
  const [choice, setChoice] = useState<"household" | "rider">();

  if (choice === "household") return <Register onBack={() => setChoice(undefined)} />;
  if (choice === "rider") return <RiderRegister onBack={() => setChoice(undefined)} />;

  return (
    <Screen
      title="Welcome to Imfucuza"
      eyebrow="MADINA, ACCRA"
      actions={
        <Button variant="quiet" onClick={signOut}>
          Sign out
        </Button>
      }
    >
      <p className="lead">How will you use the app?</p>
      <button type="button" className="hero tap choice" onClick={() => setChoice("household")}>
        <div>
          <div className="hero-title">My household needs pickups</div>
          <div className="hero-sub">Waste collected at your door, and points for sorted plastic</div>
        </div>
        <Icon name="forward" />
      </button>
      <button type="button" className="card tap choice" onClick={() => setChoice("rider")}>
        <div>
          <strong className="big">I ride a tricycle</strong>
          <div className="muted">Get pickup jobs near you and earn per pickup</div>
        </div>
        <Icon name="forward" />
      </button>
    </Screen>
  );
}
