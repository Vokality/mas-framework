import type { ActivityEventResponse } from "../api/client";
import {
  describeActivityEvent,
  describeActivityMeta,
} from "../assets/activity";

type ClientActivityPanelProps = {
  events: Array<ActivityEventResponse>;
};

export function ClientActivityPanel({ events }: ClientActivityPanelProps) {
  return (
    <article className="card">
      <h3>Latest Visibility</h3>
      {events.length === 0 ? (
        <p className="muted-copy">
          No projected network visibility has arrived for this client yet.
        </p>
      ) : (
        <div className="list">
          {events.slice(0, 6).map((event) => (
            <div className="list-item" key={event.activity_id}>
              <span className="activity-label">{describeActivityEvent(event)}</span>
              <span>{describeActivityMeta(event)}</span>
            </div>
          ))}
        </div>
      )}
    </article>
  );
}
