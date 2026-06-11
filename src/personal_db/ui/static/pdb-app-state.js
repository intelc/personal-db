(() => {
  const islands = new Map();

  async function requestJson(url, options = {}) {
    const headers = {
      Accept: 'application/json',
      ...(options.headers || {}),
    };
    const request = {
      ...options,
      headers,
    };
    if (Object.prototype.hasOwnProperty.call(options, 'data')) {
      request.method = request.method || 'POST';
      request.headers = {
        ...headers,
        'Content-Type': 'application/json',
      };
      request.body = JSON.stringify(options.data);
      delete request.data;
    }
    const response = await fetch(url, request);
    const text = await response.text();
    let body = null;
    if (text) {
      try {
        body = JSON.parse(text);
      } catch (_error) {
        body = text;
      }
    }
    if (!response.ok) {
      const detail = body && typeof body === 'object' && body.detail ? body.detail : body;
      throw new Error(detail || `Request failed (${response.status})`);
    }
    return body;
  }

  function createStore(initialState, reducer) {
    let state = initialState;
    const listeners = new Set();

    function emit() {
      listeners.forEach((listener) => listener(state));
    }

    return {
      getState() {
        return state;
      },
      setState(nextState) {
        state = typeof nextState === 'function' ? nextState(state) : nextState;
        emit();
        return state;
      },
      dispatch(action) {
        if (typeof reducer !== 'function') {
          throw new Error('dispatch requires a reducer');
        }
        state = reducer(state, action);
        emit();
        return state;
      },
      subscribe(listener) {
        listeners.add(listener);
        return () => listeners.delete(listener);
      },
    };
  }

  function parseProps(el) {
    const raw = el.getAttribute('data-pdb-island-props');
    if (!raw) return {};
    try {
      return JSON.parse(raw);
    } catch (_error) {
      return {};
    }
  }

  function registerIsland(name, mount) {
    islands.set(name, mount);
  }

  function hasIsland(name) {
    return islands.has(name);
  }

  function mountIslands(root = document) {
    root.querySelectorAll('[data-pdb-island]').forEach((el) => {
      if (el.dataset.pdbIslandReady === '1') return;
      const name = el.dataset.pdbIsland || '';
      const mount = islands.get(name);
      if (!mount) return;
      el.dataset.pdbIslandReady = '1';
      mount(el, parseProps(el));
    });
  }

  window.pdbApp = {
    createStore,
    hasIsland,
    mountIslands,
    registerIsland,
    requestJson,
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => mountIslands());
  } else {
    mountIslands();
  }
})();
