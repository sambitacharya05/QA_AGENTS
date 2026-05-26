---
name: graph-visualization
description: Skill to format graph data and render interactive dark-themed network visualizations inside VS Code.
---
# Agent Skill: Context Graph Visualization Template

This skill teaches the `@build_context` agent how to compile the graph store into a premium, interactive network diagram rendered inside a VS Code Webview.

---

## 🎨 Design Theme & Aesthetics

The visualization page must look stunning, adopting a futuristic dark-mode theme with curated colors:

1. **Background**: Sleek dark space (`#0f172a` slate).
2. **Node Colors**:
   * `business_rule`: Harmonious golden yellow (`#eab308` border, `#fef08a` body).
   * `product_feature`: Vibrant emerald green (`#10b981` border, `#a7f3d0` body).
   * `api_endpoint`: Tech-focused indigo blue (`#3b82f6` border, `#bfdbfe` body).
   * `data_model`: Creative purple (`#8b5cf6` border, `#ddd6fe` body).
   * `code_component`: Sophisticated dark slate (`#64748b` border, `#e2e8f0` body).
   * `test_scenario`: Warning coral red (`#ef4444` border, `#fecaca` body).
3. **Edge Styling**:
   * Directed arrows pointing from source to target.
   * Edge labels (e.g. `IMPLEMENTS`, `TESTS`) rendered clearly in gray with small text.
   * Smooth bezier curves.

---

## 🛠️ Vis.js HTML Generation Template

The `/view` command generates an HTML string matching this layout:

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Semantic Context Graph</title>
    <!-- Load Vis.js via CDN -->
    <script type="text/javascript" src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
    <style>
        body {
            margin: 0;
            padding: 0;
            background-color: #0f172a;
            color: #f8fafc;
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
            overflow: hidden;
        }
        #network-container {
            width: 100vw;
            height: 100vh;
            position: absolute;
            top: 0;
            left: 0;
        }
        /* Premium Side Detail Drawer */
        #detail-drawer {
            position: absolute;
            top: 20px;
            right: 20px;
            width: 350px;
            max-height: 90vh;
            background: rgba(30, 41, 59, 0.85);
            backdrop-filter: blur(12px);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 12px;
            padding: 20px;
            box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.3);
            overflow-y: auto;
            display: none;
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            z-index: 1000;
        }
        h2 { margin-top: 0; color: #f1f5f9; font-size: 1.25rem; border-bottom: 1px solid rgba(255, 255, 255, 0.1); padding-bottom: 8px; }
        .property { margin-bottom: 12px; }
        .label { font-size: 0.75rem; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em; }
        .value { font-size: 0.9rem; color: #cbd5e1; white-space: pre-wrap; word-break: break-all; }
        .close-btn { position: absolute; top: 10px; right: 10px; cursor: pointer; color: #94a3b8; background: none; border: none; font-size: 1.1rem; }
    </style>
</head>
<body>
    <div id="network-container"></div>
    <div id="detail-drawer">
        <button class="close-btn" onclick="closeDrawer()">&times;</button>
        <h2 id="drawer-title">Node details</h2>
        <div class="property"><div class="label">Type</div><div class="value" id="drawer-type">-</div></div>
        <div class="property"><div class="label">Description</div><div class="value" id="drawer-desc">-</div></div>
        <div class="property"><div class="label">Metadata</div><div class="value" id="drawer-meta">-</div></div>
    </div>

    <script type="text/javascript">
        // Data injected by the VS Code extension
        const nodesData = __NODES_JSON__;
        const edgesData = __EDGES_JSON__;

        const container = document.getElementById('network-container');
        const data = {
            nodes: new vis.DataSet(nodesData),
            edges: new vis.DataSet(edgesData)
        };
        const options = {
            nodes: {
                shape: 'dot',
                size: 24,
                font: { size: 14, color: '#f8fafc', face: 'Inter' },
                borderWidth: 2
            },
            edges: {
                arrows: { to: { enabled: true, scaleFactor: 1 } },
                color: { color: '#475569', highlight: '#38bdf8' },
                font: { size: 10, align: 'middle', color: '#64748b', strokeWidth: 0 },
                smooth: { type: 'cubicBezier', forceDirection: 'none', roundness: 0.5 }
            },
            physics: {
                barnesHut: { gravitationalConstant: -3000, centralGravity: 0.3, springLength: 150 },
                stabilization: { iterations: 150 }
            }
        };

        const network = new vis.Network(container, data, options);

        // Click handler to open details drawer
        network.on("click", function (params) {
            if (params.nodes.length > 0) {
                const nodeId = params.nodes[0];
                const node = nodesData.find(n => n.id === nodeId);
                if (node) {
                    document.getElementById('drawer-title').innerText = node.label || node.name;
                    document.getElementById('drawer-type').innerText = node.type.toUpperCase();
                    document.getElementById('drawer-desc').innerText = node.description || 'No description provided.';
                    document.getElementById('drawer-meta').innerText = JSON.stringify(node.metadata, null, 2);
                    document.getElementById('detail-drawer').style.display = 'block';
                }
            }
        });

        function closeDrawer() {
            document.getElementById('detail-drawer').style.display = 'none';
        }
    </script>
</body>
</html>
```
