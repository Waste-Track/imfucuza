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
import { RiderPending } from "./screens/rider/RiderPending";
import { RiderHome } from "./screens/rider/RiderHome";
import { RiderJob } from "./screens/rider/RiderJob";
import { Console } from "./screens/supervisor/Console";
import { useSession } from "./session";

/** One app for both roles: who signed in decides which screens open. */
export function App() {
  const { session, me, meError, refreshMe, signOut } = useSession();

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
        <Routes>
          <Route path="/" element={<RiderHome />} />
          <Route path="/jobs/:id" element={<RiderJob />} />
          <Route path="/account" element={<Account />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
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
