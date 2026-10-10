// Event-socket client with auto-reconnect. Usage: const ev = connectEvents(onEvent, onStatus); ev.send({...})
export function wsUrl(path) {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}${path}`;
}

export function connectEvents(onEvent, onStatus) {
  let ws;
  let closed = false;
  let retry = 0;
  const queue = [];

  const open = () => {
    ws = new WebSocket(wsUrl("/ws/events"));
    ws.onopen = () => {
      retry = 0;
      onStatus?.("connected");
      while (queue.length) ws.send(queue.shift());
    };
    ws.onmessage = (m) => onEvent(JSON.parse(m.data));
    ws.onclose = () => {
      onStatus?.("disconnected");
      if (!closed) setTimeout(open, Math.min(500 * 2 ** retry++, 5000));
    };
  };
  open();

  return {
    send(msg) {
      const data = JSON.stringify(msg);
      if (ws.readyState === WebSocket.OPEN) ws.send(data);
      else queue.push(data);
    },
    close() {
      closed = true;
      ws.close();
    },
  };
}
