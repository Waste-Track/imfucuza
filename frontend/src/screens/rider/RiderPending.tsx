import { useState } from "react";
import { Icon } from "../../components/Icon";
import { Button, Card, Screen } from "../../components/ui";
import { useSession } from "../../session";

export function RiderPending() {
  const { me, refreshMe, signOut } = useSession();
  const [checking, setChecking] = useState(false);

  const check = async () => {
    setChecking(true);
    await refreshMe();
    setChecking(false);
  };

  return (
    <Screen title="Application sent" eyebrow="RIDER">
      <Card tone="green">
        <div className="row">
          <Icon name="clock" size={32} />
          <strong className="big">Waiting for approval</strong>
        </div>
        <p>
          {me?.name ? `Thanks, ${me.name.split(" ")[0]}. ` : ""}Your registration has been received.
          You'll get an SMS when you're approved.
        </p>
      </Card>
      <Button variant="secondary" busy={checking} onClick={check}>
        Check again
      </Button>
      <Button variant="quiet" onClick={signOut}>
        Sign out
      </Button>
    </Screen>
  );
}
