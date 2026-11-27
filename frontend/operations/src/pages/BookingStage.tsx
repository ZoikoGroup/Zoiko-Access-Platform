import { useEffect, useState } from "react";
import { StageList } from "@shared/StageList";
import { getBookingIntent, getBookingStages, getProviderStage, type BookingIntent, type ProviderStage } from "../api";
import { Pill } from "../ui";

const PROVIDER: Record<string, string> = {
  awaiting_provider: "Asked, waiting for the booking system to answer", refused: "The booking system refused it",
  confirmed: "Confirmed", cancelled: "Cancelled", requested: "Requested",
};
const words = (code: string) => code.toLowerCase().replace(/_/g, " ");

/** Where one appointment stands: its standard booking stage, who confirmed it, and what its
 * clinic's booking system has said (if it was ever asked). */
export function BookingStage({ id }: { id: string }) {
  const [data, setData] = useState<{ intent: BookingIntent; stage: ProviderStage | null } | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    Promise.all([getBookingIntent(id), getProviderStage(id)])
      .then(([intent, stage]) => { if (live) setData({ intent, stage }); })
      .catch((e: unknown) => { if (live) setError(e instanceof Error ? e.message : "Could not load the stage"); });
    return () => { live = false; };
  }, [id]);
  if (error) return <div className="error" role="alert">{error}</div>;
  if (!data) return <div className="empty">Loading…</div>;
  const { intent, stage } = data;
  return (
    <div className="appt">
      <div className="info">
        <b>{intent.label} <Pill kind={intent.final ? "info" : "warn"}>{intent.final ? "Final" : "Open"}</Pill></b>
        <small>{intent.source_confirmation
          ? `Confirmed by ${intent.source_confirmation.by === "booking_system" ? "the booking system" : "the clinic"}.`
          : "Not confirmed by anyone."}</small>
        <small>{stage
          ? `Booking system: ${PROVIDER[stage.stage] ?? words(stage.stage)}${stage.operation ? ` (latest question: ${words(stage.operation)})` : ""}.`
          : "No booking system has been asked about this appointment."}</small>
        {intent.next.length > 0 && <small>What may happen next: {intent.next.map(words).join(", ")}.</small>}
      </div>
      <StageList label="this appointment" load={() => getBookingStages(id)} />
    </div>
  );
}
