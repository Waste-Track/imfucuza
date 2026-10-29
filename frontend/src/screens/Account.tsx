import { Icon } from "../components/Icon";
import { Button, Card, HouseholdNav, Screen } from "../components/ui";
import { useSession } from "../session";

export function Account() {
  const { me, session, signOut } = useSession();
  return (
    <Screen title="Account" nav={me?.role === "household" ? <HouseholdNav /> : undefined} back={me?.role === "rider" ? "/" : undefined}>
      <Card>
        <dl className="facts">
          {me?.name && (
            <div>
              <dt>Name</dt>
              <dd>{me.name}</dd>
            </div>
          )}
          <div>
            <dt>Phone</dt>
            <dd>{session?.user.phone ? `+${session.user.phone.replace(/^\+/, "")}` : "–"}</dd>
          </div>
        </dl>
      </Card>
      <p className="muted small">
        To change your address or remove your data, call or visit the Imfucuza supervisor in Madina.
      </p>
      <Button variant="secondary" onClick={signOut}>
        <Icon name="signout" />
        Sign out
      </Button>
    </Screen>
  );
}
