/* ==========================================================================
   BIS COMPLIANCE COPILOT & ACTION AGENT FRONTEND CONTROLLER
   ========================================================================== */

document.addEventListener('DOMContentLoaded', () => {
  const chatForm = document.getElementById('copilotChatForm');
  const chatInput = document.getElementById('chatInput');
  const chatHistory = document.getElementById('chatHistory');
  const treeContainer = document.getElementById('decisionTreeContainer');
  const citationDrawer = document.getElementById('citationDrawerContainer');
  const actionContainer = document.getElementById('actionAgentContainer');

  if (!chatForm) return;

  const initialQuery = new URLSearchParams(window.location.search).get('query');
  if (initialQuery && chatInput) {
    chatInput.value = initialQuery;
    window.setTimeout(() => chatForm.requestSubmit(), 0);
  }

  chatForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const query = chatInput.value.trim();
    if (!query) return;

    // 1. Append User Message Bubble
    appendMessage('user', query);
    chatInput.value = '';

    // Show Loading Spinner Bubble
    const loadingId = appendLoadingBubble();

    try {
      // 2. Call Flask API
      const response = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: query })
      });

      const data = await response.json();
      removeBubble(loadingId);

      if (data.status === 'success') {
        // 3. Render Copilot Response Bubble
        appendMessage('copilot', data.answer, data.distinction, data.citations);

        // 4. Update "Show Me Why" Decision Tree
        if (data.decision_tree) {
          renderDecisionTree(data.decision_tree);
        }

        // 5. Update Citation Drawer
        if (data.citations && data.citations.length > 0) {
          renderCitations(data.citations);
        }

        // 6. Update Action Agent Gateway Recommendations
        if (data.action_recommendations) {
          renderActionRecommendations(data.action_recommendations, data.matched_standard);
        }
      } else {
        appendMessage('copilot', 'Sorry, I encountered an error analyzing compliance data.');
      }
    } catch (err) {
      removeBubble(loadingId);
      appendMessage('copilot', 'Network connection error. Please check backend connection.');
    }
  });

  function appendMessage(sender, text, distinction = null, citations = []) {
    const bubble = document.createElement('div');
    bubble.className = `chat-bubble chat-bubble-${sender}`;

    if (sender === 'copilot' && distinction) {
      const badgeHtml = `<div style="margin-bottom: 8px;">
        <span class="badge badge-deterministic">${distinction.rule_type}</span>
        <span class="badge badge-ai" style="margin-left: 6px;">AI RAG GROUNDED</span>
      </div>`;
      bubble.innerHTML = badgeHtml + formatMarkdownText(text);
    } else {
      bubble.innerHTML = formatMarkdownText(text);
    }

    chatHistory.appendChild(bubble);
    chatHistory.scrollTop = chatHistory.scrollHeight;
  }

  function appendLoadingBubble() {
    const id = 'loading_' + Date.now();
    const bubble = document.createElement('div');
    bubble.id = id;
    bubble.className = 'chat-bubble chat-bubble-copilot';
    bubble.innerHTML = '<span style="color: var(--accent-cyan);">⚡ Querying BIS Knowledge Base & Hybrid RAG Engine...</span>';
    chatHistory.appendChild(bubble);
    chatHistory.scrollTop = chatHistory.scrollHeight;
    return id;
  }

  function removeBubble(id) {
    const el = document.getElementById(id);
    if (el) el.remove();
  }

  function formatMarkdownText(text) {
    // Basic Markdown formatting for headings, bold, bullet points
    return text
      .replace(/### (.*?)\n/g, '<h4 style="color: var(--accent-cyan); margin: 8px 0;">$1</h4>')
      .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
      .replace(/- (.*?)\n/g, '<li style="margin-left: 16px;">$1</li>');
  }

  function renderDecisionTree(tree) {
    if (!treeContainer) return;
    treeContainer.innerHTML = '<h3 style="font-size: 0.95rem; color: var(--text-muted); margin-bottom: 12px;">Interactive "Show Me Why" Reasoning Flow</h3>';

    tree.nodes.forEach((node, idx) => {
      const nodeEl = document.createElement('div');
      nodeEl.className = 'tree-node';
      nodeEl.innerHTML = `
        <div style="width: 28px; height: 28px; border-radius: 50%; background: rgba(56, 189, 248, 0.2); display: flex; align-items: center; justify-content: center; font-weight: 700; color: var(--accent-cyan); font-size: 0.8rem;">
          ${idx + 1}
        </div>
        <div style="flex: 1;">
          <div style="font-weight: 600; font-size: 0.88rem; color: white;">${node.label}</div>
          ${node.sublabel ? `<div style="font-size: 0.78rem; color: var(--text-muted);">${node.sublabel}</div>` : ''}
        </div>
        <span class="badge ${node.type === 'deterministic' ? 'badge-deterministic' : 'badge-ai'}">${node.badge}</span>
      `;
      treeContainer.appendChild(nodeEl);

      if (idx < tree.nodes.length - 1) {
        const connector = document.createElement('div');
        connector.className = 'tree-node-connector';
        treeContainer.appendChild(connector);
      }
    });
  }

  function renderCitations(citations) {
    if (!citationDrawer) return;
    citationDrawer.innerHTML = '<h4 style="font-size: 0.9rem; color: var(--text-muted); margin-bottom: 8px;">Verifiable BIS Source Evidence</h4>';

    citations.forEach(c => {
      const item = document.createElement('div');
      item.className = 'citation-drawer';
      item.innerHTML = `
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <strong style="color: var(--accent-cyan); font-size: 0.85rem;">[CITATION ${c.citation_id}] ${c.doc_code}</strong>
          <span style="font-size: 0.75rem; color: var(--text-muted);">Page ${c.page_number}</span>
        </div>
        <div style="font-size: 0.8rem; color: var(--text-muted); margin-top: 4px;"><strong>Section:</strong> ${c.section_heading}</div>
        <p style="font-size: 0.8rem; color: var(--text-main); margin-top: 6px; font-style: italic;">"${c.content_snippet}"</p>
        ${c.doc_url ? `<a href="${c.doc_url}" target="_blank" style="font-size: 0.75rem; color: var(--accent-blue); text-decoration: underline; display: inline-block; margin-top: 6px;">🔗 View Official Document (PDF)</a>` : ''}
      `;
      citationDrawer.appendChild(item);
    });
  }

  function renderActionRecommendations(actions, matchedStd) {
    if (!actionContainer) return;
    actionContainer.innerHTML = '<h3 style="font-size: 0.95rem; color: var(--text-muted); margin-bottom: 12px;">Recommended Actions (Permission Gateway)</h3>';

    actions.forEach(act => {
      const card = document.createElement('div');
      card.className = 'glass-card';
      card.style.padding = '14px';
      card.style.marginBottom = '10px';

      if (act.level === 1) {
        card.innerHTML = `
          <div style="display: flex; justify-content: space-between; align-items: center;">
            <div>
              <strong style="font-size: 0.88rem; color: white;">${act.title}</strong>
              <div style="font-size: 0.78rem; color: var(--text-muted);">${act.description}</div>
            </div>
            <a href="${act.url}" target="_blank" class="btn-pill btn-glass" style="font-size: 0.78rem;">Open Portal ↗</a>
          </div>
        `;
      } else if (act.level === 2) {
        card.innerHTML = `
          <div style="display: flex; justify-content: space-between; align-items: center;">
            <div>
              <strong style="font-size: 0.88rem; color: white;">${act.title}</strong>
              <div style="font-size: 0.78rem; color: var(--text-muted);">${act.description}</div>
            </div>
            <button class="btn-pill btn-primary" onclick="createComplianceCase('${matchedStd ? matchedStd.title : 'Product'}', '${matchedStd ? matchedStd.is_number : 'IS Standard'}')" style="font-size: 0.78rem;">Create Case & PDF</button>
          </div>
        `;
      } else if (act.level === 3) {
        card.innerHTML = `
          <div style="display: flex; justify-content: space-between; align-items: center;">
            <div>
              <span class="badge badge-warning" style="margin-bottom: 4px;">HIGH RISK - APPROVAL REQUIRED</span>
              <div style="font-weight: 600; font-size: 0.88rem; color: white;">${act.title}</div>
              <div style="font-size: 0.78rem; color: var(--text-muted);">${act.description}</div>
            </div>
            <button class="btn-pill btn-primary" onclick="triggerApprovalModal('${act.lab_name}', '${act.lab_email}', '${matchedStd ? matchedStd.is_number : 'IS Standard'}')" style="font-size: 0.78rem;">Review Draft & Send</button>
          </div>
        `;
      }
      actionContainer.appendChild(card);
    });
  }
});

// Global Handlers for Modal & Case Creation
window.createComplianceCase = async function(productName, isNumber) {
  try {
    const res = await fetch('/api/case/create', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ product_name: productName, is_number: isNumber })
    });
    const data = await res.json();
    if (data.status === 'success') {
      window.location.href = `/cases/${data.case_id}`;
    }
  } catch (err) {
    alert("Error creating compliance case.");
  }
};

window.triggerApprovalModal = function(labName, labEmail, isNumber) {
  const modal = document.getElementById('approvalModal');
  const labNameEl = document.getElementById('modalLabName');
  const labEmailEl = document.getElementById('modalLabEmail');
  const draftBody = document.getElementById('modalDraftBody');

  if (labNameEl) labNameEl.textContent = labName;
  if (labEmailEl) labEmailEl.textContent = labEmail;
  if (draftBody) draftBody.value = `Dear ${labName} Team,\n\nWe request formal type testing fee quote and sample quantity requirement for product certification under ${isNumber}.\n\nPlease respond at your earliest convenience.\n\nRegards,\nCompliance Officer`;

  if (modal) modal.classList.add('active');
};

window.closeApprovalModal = function() {
  const modal = document.getElementById('approvalModal');
  if (modal) modal.classList.remove('active');
};

window.dispatchApprovedEnquiry = async function() {
  const modal = document.getElementById('approvalModal');
  try {
    const res = await fetch('/api/actions/execute', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action_id: 'LAB_ENQUIRY_DISPATCH', approved: true })
    });
    const data = await res.json();
    alert("✅ Action Authorized! Laboratory enquiry successfully dispatched.");
    if (modal) modal.classList.remove('active');
  } catch (err) {
    alert("Action authorization failed.");
  }
};
