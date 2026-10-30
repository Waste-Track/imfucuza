import { Navigate, Route, Routes } from "react-router";
import { Alert, Button, Loading, Screen } from "./components/ui";
import { Account } from "./screens/Account";
import { Landing } from "./screens/Landing";
import { Welcome } from "./screens/Welcome";
import { Home } from "./screens/household/Home";
import { NewRequest } from "./screens/household/NewRequest";
import { PickupDetail } from "./screens/household/PickupDetail";
import { PlasticPhoto } from "./screens/household/PlasticPhoto";
import { Points } from "./screens/household/Points";
import { Requests } from "./screens/household/Requests";
import { RiderDutyProvider } from "./screens/rider/duty";
import { RiderEarnings } from "./screens/rider/RiderEarnings";
import { RiderHistory } from "./screens/rider/RiderHistory";
import { RiderPending } from "./screens/rider/RiderPending";
import { RiderHome } from "./screens/rider/RiderHome";
import { RiderJob } from "./screens/rider/RiderJob";
import { Console } from "./screens/supervisor/Console";
import type { Intent } from "./lib/intent";
import type { Me } from "./lib/types";
import { useSession } from "./session";

/** One app for both roles: who signed in decides which screens open. */
export function App() {
  const { session, me, meError, refreshMe, choseOther, signOut } = useSession();

  if (session === undefined) return <Splash />;
  if (!session) return <Landing />;
  if (!me) {
    if (!meError) return <Splash />;
    return (
      <Screen title="Can't reach Imfucuza">
        <Alert>{meError.message}</Alert>
        <Button onClick={refreshMe}>Try again</Button>
        <Button variant="quiet" onClick={signOut}>
          Sign out
        </Button>
      </Screen>
    );
  }

  if (choseOther && me.role) return <AlreadyRegistered chose={choseOther} role={me.role} />;
  if (me.role === "rider" && me.status === "pending") return <RiderPending />;
  if (me.status && me.status !== "active") {
    return <Blocked title="Account paused" text="Your account is not active. Call the Imfucuza supervisor in Madina." />;
  }

  switch (me.role) {
    case null:
      return <Welcome />;
    case "household":
      return (
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/request" element={<NewRequest />} />
          <Route path="/pickups" element={<Requests />} />
          <Route path="/pickups/:id" element={<PickupDetail />} />
          <Route path="/pickups/:id/photo" element={<PlasticPhoto />} />
          <Route path="/points" element={<Points />} />
          <Route path="/account" element={<Account />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      );
    case "rider":
      return (
        <RiderDutyProvider>
          <Routes>
            <Route path="/" element={<RiderHome />} />
            <Route path="/jobs/:id" element={<RiderJob />} />
            <Route path="/history" element={<RiderHistory />} />
            <Route path="/earnings" element={<RiderEarnings />} />
            <Route path="/account" element={<Account />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </RiderDutyProvider>
      );
    case "supervisor":
    case "admin":
      return <Console />;
  }
}

function Splash() {
  return (
    <div className="splash">
      <Loading label="Opening Imfucuza" />
    </div>
  );
}

const ROLE_NAMES = { household: "a household", rider: "a rider", supervisor: "a supervisor", admin: "an admin" };

/** They signed up for one thing with a number that already holds another account. */
function AlreadyRegistered({ chose, role }: { chose: Intent; role: NonNullable<Me["role"]> }) {
  const { acknowledge, signOut } = useSession();
  return (
    <Screen title="This number is already registered">
      <p className="lead">
        It's registered as {ROLE_NAMES[role]}, so you've been signed in to that account. Each number
        can only have one account. To register as {ROLE_NAMES[chose]}, use a different number.
      </p>
      <Button onClick={acknowledge}>Continue as {ROLE_NAMES[role]}</Button>
      <Button variant="secondary" onClick={signOut}>
        Use a different number
      </Button>
    </Screen>
  );
}

function Blocked({ title, text }: { title: string; text: string }) {
  const { signOut } = useSession();
  return (
    <Screen title={title}>
      <p className="lead">{text}</p>
      <Button variant="secondary" onClick={signOut}>
        Sign out
      </Button>
    </Screen>
  );
}
