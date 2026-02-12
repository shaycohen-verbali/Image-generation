import React, { useMemo } from 'react'

const NODE_WIDTH = 190
const NODE_HEIGHT = 92

function nodeById(nodes) {
  const index = new Map()
  nodes.forEach((node) => index.set(node.id, node))
  return index
}

function point(node, side) {
  if (side === 'left') return { x: node.x, y: node.y + NODE_HEIGHT / 2 }
  if (side === 'right') return { x: node.x + NODE_WIDTH, y: node.y + NODE_HEIGHT / 2 }
  if (side === 'top') return { x: node.x + NODE_WIDTH / 2, y: node.y }
  return { x: node.x + NODE_WIDTH / 2, y: node.y + NODE_HEIGHT }
}

function edgePath(edge, fromNode, toNode) {
  if (!fromNode || !toNode) return ''

  if (edge.type === 'loop') {
    const start = point(fromNode, 'top')
    const end = point(toNode, 'top')
    const lift = 86
    return `M ${start.x} ${start.y} C ${start.x} ${start.y - lift}, ${end.x} ${end.y - lift}, ${end.x} ${end.y}`
  }

  const start = point(fromNode, 'right')
  const end = point(toNode, 'left')
  const horizontalGap = Math.abs(end.x - start.x)
  const needsElbow = Math.abs(end.y - start.y) > 8 || horizontalGap < 60

  if (!needsElbow) {
    return `M ${start.x} ${start.y} L ${end.x} ${end.y}`
  }

  const midX = start.x + Math.max(34, horizontalGap / 2)
  return `M ${start.x} ${start.y} L ${midX} ${start.y} L ${midX} ${end.y} L ${end.x} ${end.y}`
}

function labelPoint(edge, fromNode, toNode) {
  if (!fromNode || !toNode) return { x: 0, y: 0 }
  if (edge.type === 'loop') {
    return {
      x: (fromNode.x + toNode.x + NODE_WIDTH) / 2,
      y: Math.min(fromNode.y, toNode.y) - 72,
    }
  }
  return {
    x: (fromNode.x + toNode.x + NODE_WIDTH) / 2,
    y: (fromNode.y + toNode.y + NODE_HEIGHT) / 2 - 10,
  }
}

export default function WorkflowCanvas({
  nodes,
  edges,
  width = 1380,
  height = 320,
  selectedNodeId = '',
  onSelectNode,
}) {
  const byId = useMemo(() => nodeById(nodes), [nodes])

  return (
    <div className="workflow-wrap">
      <div className="workflow-scroller">
        <div className="workflow-canvas" style={{ width: `${width}px`, height: `${height}px` }}>
          <svg className="workflow-svg" width={width} height={height} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none">
            <defs>
              <marker id="wf-arrow" markerWidth="10" markerHeight="8" refX="9" refY="4" orient="auto">
                <path d="M0,0 L10,4 L0,8 z" fill="#4d6370" />
              </marker>
            </defs>

            {edges.map((edge) => {
              const fromNode = byId.get(edge.from)
              const toNode = byId.get(edge.to)
              const path = edgePath(edge, fromNode, toNode)
              if (!path) return null
              const label = labelPoint(edge, fromNode, toNode)
              return (
                <g key={`${edge.from}-${edge.to}-${edge.label}`}>
                  <path
                    className={edge.type === 'loop' ? 'workflow-edge workflow-edge-loop' : 'workflow-edge'}
                    d={path}
                    markerEnd="url(#wf-arrow)"
                  />
                  {edge.label ? (
                    <text x={label.x} y={label.y} className="workflow-edge-label">
                      {edge.label}
                    </text>
                  ) : null}
                </g>
              )
            })}
          </svg>

          {nodes.map((node) => (
            <button
              key={node.id}
              className={
                node.id === selectedNodeId
                  ? `workflow-node selected status-${node.status || 'queued'}`
                  : `workflow-node status-${node.status || 'queued'}`
              }
              style={{ left: `${node.x}px`, top: `${node.y}px`, width: `${NODE_WIDTH}px`, minHeight: `${NODE_HEIGHT}px` }}
              onClick={() => onSelectNode?.(node.id)}
            >
              <span className="workflow-node-port port-left" />
              <span className="workflow-node-port port-right" />
              <span className="workflow-node-title">{node.label}</span>
              {node.subtitle ? <span className="workflow-node-subtitle">{node.subtitle}</span> : null}
              {node.badge ? <span className="workflow-node-badge">{node.badge}</span> : null}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
