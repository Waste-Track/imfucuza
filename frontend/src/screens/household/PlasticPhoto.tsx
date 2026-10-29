import { useNavigate, useParams } from "react-router";
import { PhotoCapture } from "../../components/PhotoCapture";
import { Alert, Loading, Screen } from "../../components/ui";
import { api } from "../../lib/api";
import { kg } from "../../lib/format";
import { usePoll } from "../../lib/usePoll";

export function PlasticPhoto() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const { data: pickup, error } = usePoll(() => api.pickup(id), 0, [id]);

  return (
    <Screen title="Photo of your plastic" back={`/pickups/${id}`} dark>
      {!pickup ? (
        error ? <Alert>{error.message}</Alert> : <Loading />
      ) : !pickup.photo_wanted || pickup.weight_g == null ? (
        <Alert tone="info">No photo is needed for this pickup right now.</Alert>
      ) : (
        <PhotoCapture
          instructions={`The rider weighed ${kg(pickup.weight_g)}. Show the whole bag, with its printed code, in the frame.`}
          sendText="Send photo"
          send={async (photo) => {
            await api.householdPhoto(pickup.id, pickup.weight_g!, photo);
            navigate(`/pickups/${pickup.id}`, { replace: true });
          }}
        />
      )}
    </Screen>
  );
}
