const root = document.getElementById('root');
root.innerHTML = `
  <div class="container">
    <h1>Agentic Autoscaler POC</h1>
    <p>Hybrid autoscaler: telemetry + optional Gemini LLM planner.</p>
    <img src="../diagram.svg" alt="architecture diagram" style="max-width:100%;height:auto;border:1px solid #ccc" />
    <h2>How it works</h2>
    <ol>
      <li>Monitor probes the LB and publishes p95 windows to Redis.</li>
      <li>Planner learns a rolling baseline and may call Gemini to decide actions.</li>
      <li>Executor starts/stops replicas; Watcher updates nginx upstreams.</li>
      <li>Dashboard & Subscriber show recent telemetry & actions.</li>
    </ol>
    <p>See README.md for details and env examples.</p>
  </div>
`;
