(() => {
  const activeRequests = new WeakMap();
  const isAuthentic = result => String(result || '').trim().toLowerCase() === 'authentic';

  window.SealGuardAuditVerification = {
    async load({logId, resultElement, section, list, initialResult = '', compareLink = null}) {
      const request = {};
      activeRequests.set(list, request);
      const current = () => activeRequests.get(list) === request;
      if (compareLink) compareLink.hidden = true;
      resultElement.textContent = initialResult || 'Not evaluated';
      section.hidden = !initialResult || isAuthentic(initialResult);
      list.replaceChildren();
      if (!logId) return;
      try {
        const response = await fetch('/dashboard/activity/' + encodeURIComponent(logId) + '/verification-history/', {credentials: 'same-origin'});
        if (!response.ok) throw new Error();
        const data = await response.json();
        if (!current()) return;
        // Offered only for Possible Modification reports with a preserved copy.
        if (compareLink && data.compare_url) {
          compareLink.href = data.compare_url;
          compareLink.hidden = false;
        }
        const selectedResult = data.selected_result || initialResult;
        resultElement.textContent = selectedResult || 'Not evaluated';
        section.hidden = !selectedResult || isAuthentic(selectedResult);
        if (section.hidden) return;
        const events = (data.events || []).filter(event => !isAuthentic(event.result));
        if (!events.length) {
          list.textContent = 'No verification trace was stored for this activity.';
          return;
        }
        for (const event of events) {
          const item = document.createElement('details');
          item.className = 'verification-history-item';
          const summary = document.createElement('summary');
          summary.className = 'verification-history-toggle';
          const date = document.createElement('span');
          date.className = 'verification-history-date';
          date.textContent = event.verified_at + (event.is_selected ? ' · Selected activity' : '');
          const result = document.createElement('span');
          result.className = 'verification-history-result warning';
          result.textContent = event.result;
          summary.append(date, result);
          const detail = document.createElement('div');
          detail.className = 'verification-history-detail';
          item.append(summary, detail);
          list.append(item);
          let loading = false, loaded = false;
          item.addEventListener('toggle', async () => {
            if (!item.open || loading || loaded || !current()) return;
            loading = true;
            detail.textContent = 'Loading verification trace...';
            try {
              const response = await fetch('/dashboard/activity/' + encodeURIComponent(event.id) + '/verification-log/', {credentials: 'same-origin'});
              if (!response.ok) throw new Error();
              const log = await response.json();
              if (!current()) return;
              detail.replaceChildren();
              if (log.note) {
                const note = document.createElement('p');
                note.className = 'verification-history-note';
                note.textContent = log.note;
                detail.append(note);
              }
              const trace = document.createElement('pre');
              trace.className = 'verification-history-log';
              trace.textContent = log.debug_log || 'A verification trace was not stored for this older activity.';
              detail.append(trace);
              loaded = true;
            } catch (_) {
              if (current()) detail.textContent = 'The verification trace could not be loaded. Close and reopen this entry to retry.';
            } finally { loading = false; }
          });
        }
      } catch (_) {
        if (current() && !section.hidden) list.textContent = 'Verification logs could not be loaded. Reopen the document to retry.';
      }
    },
  };
})();
