(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };

  function size(n) {
    var u = ["B", "KB", "MB", "GB", "TB"], i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return (i === 0 ? n.toFixed(0) : n.toFixed(1)) + " " + u[i];
  }

  function meter() {
    var m = document.querySelector(".meter");
    if (!m) return;
    $("meter-fill").style.width = Math.min(100, parseFloat(m.dataset.used)) + "%";
    $("meter-limit").style.left = Math.max(0, Math.min(100, parseFloat(m.dataset.limit))) + "%";
  }

  var log = $("log");
  function refreshLog() {
    if (!log || document.hidden) return;
    fetch("/api/log?lines=300", { cache: "no-store" }).then(function (r) { return r.json(); }).then(function (d) {
      var atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
      log.textContent = d.text || "The log is empty. It fills as soon as a run starts.";
      if (atBottom) log.scrollTop = log.scrollHeight;
    }).catch(function () {});
  }

  function refreshStatus() {
    if (!$("state-line") || document.hidden) return;
    fetch("/api/status", { cache: "no-store" }).then(function (r) { return r.json(); }).then(function (s) {
      $("state-line").textContent = s.running
        ? "Downloading: run #" + s.run_id + ", " + s.run_new + " new so far" : "Idle";
      $("state-sub").textContent = (s.next_run ? "Next scheduled run " + s.next_run + " (daily at " + s.schedule + "). " : "") +
        s.stats.downloaded + " videos on disk, " + s.stats.total + " known to the database.";
      $("run-now").disabled = s.running;
      $("disk-free").textContent = size(s.disk.free) + " free";
      $("storage-alert").hidden = !s.storage_low;
      var m = document.querySelector(".meter");
      m.dataset.used = (s.disk.used / s.disk.total * 100);
      m.dataset.limit = ((s.disk.total - s.min_free) / s.disk.total * 100);
      meter();
    }).catch(function () {});
  }

  function refreshLiveState() {
    var el = $("live-state");
    if (!el || document.hidden) return;
    fetch("/api/status", { cache: "no-store" }).then(function (r) { return r.json(); }).then(function (s) {
      el.textContent = (s.running ? "Run #" + s.run_id + " in progress, " + s.run_new + " new video(s) so far. "
                                  : "No run in progress. ") +
        (s.next_run ? "Next scheduled run " + s.next_run + ". " : "") + s.stats.downloaded + " videos on disk.";
    }).catch(function () {});
  }
  if ($("live-state")) { refreshLiveState(); setInterval(refreshLiveState, 3000); }

  var btn = $("run-now");
  if (btn) {
    btn.addEventListener("click", function () {
      btn.disabled = true;
      fetch("/run-now", { method: "POST" }).then(function (r) { return r.json(); }).then(function (d) {
        $("run-msg").textContent = d.message;
        setTimeout(refreshStatus, 2500);
      }).catch(function () { $("run-msg").textContent = "Could not reach the server."; btn.disabled = false; });
    });
  }

  meter();
  if (log) { refreshLog(); setInterval(refreshLog, 2000); log.scrollTop = log.scrollHeight; }
  if ($("state-line")) setInterval(refreshStatus, 3000);
})();
