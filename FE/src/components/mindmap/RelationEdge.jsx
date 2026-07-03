// Mindmap viewer — v2 relation edge (semantic, non-tree link between nodes).
// New in Task 14: dashed line in the seal/son accent color (var(--accent), the same
// token used for the app's one stamped action — see index.css "Seal = the one
// stamped action"), with a small label at the midpoint. Rendered as ReactFlow
// edge type "relation", kept separate from tree edges so it can be toggled
// independently ("Quan hệ" toolbar toggle).
import { BaseEdge, EdgeLabelRenderer, getBezierPath } from "reactflow";

const RelationEdge = ({
  id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, markerEnd,
}) => {
  const [path, labelX, labelY] = getBezierPath({
    sourceX, sourceY, sourcePosition, targetX, targetY, targetPosition,
  });

  const label = data?.label || "";

  return (
    <>
      <BaseEdge
        id={id}
        path={path}
        markerEnd={markerEnd}
        style={{
          fill: "none",
          stroke: "var(--accent)",
          strokeWidth: 1.4,
          strokeDasharray: "6 4",
          opacity: 0.7,
        }}
      />
      {label && (
        <EdgeLabelRenderer>
          <div
            style={{
              position: "absolute",
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
              pointerEvents: "none",
              color: "var(--accent)",
              border: "1px solid color-mix(in srgb, var(--accent) 35%, transparent)",
              background: "color-mix(in srgb, var(--accent) 10%, var(--bg-card, #fff))",
            }}
            className="rounded-full px-1.5 py-0.5 text-[9px] font-medium whitespace-nowrap"
          >
            {label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
};

export default RelationEdge;
