/* ═══════════════════════════════════════════════════════════════════════════
   Resources (Notes Platform) – PrepPulse
   ═══════════════════════════════════════════════════════════════════════════ */
(function () {
    "use strict";

    // ── DOM refs ──
    const uploadForm = document.querySelector("[data-upload-form]");
    const uploadAlert = document.querySelector("[data-upload-alert]");
    const fileInput = document.getElementById("res-file");
    const filePickBtn = document.querySelector("[data-file-pick]");
    const fileNameDisplay = document.querySelector("[data-file-name]");
    const resourcesList = document.querySelector("[data-resources-list]");
    const resourcesEmpty = document.querySelector("[data-resources-empty]");
    const tabs = document.querySelectorAll("[data-res-tab]");
    const filterBranch = document.querySelector("[data-filter-branch]");
    const filterYear = document.querySelector("[data-filter-year]");
    const filterSubject = document.querySelector("[data-filter-subject]");
    const statTotal = document.querySelector("[data-total-resources]");
    const statMy = document.querySelector("[data-my-uploads]");
    const statPending = document.querySelector("[data-pending-count]");

    let currentTab = "all";

    // ── File picker ──
    if (filePickBtn && fileInput) {
        filePickBtn.addEventListener("click", () => fileInput.click());
        fileInput.addEventListener("change", () => {
            if (fileInput.files.length > 0) {
                fileNameDisplay.textContent = fileInput.files[0].name;
                fileNameDisplay.classList.add("has-file");
            } else {
                fileNameDisplay.textContent = "No file chosen";
                fileNameDisplay.classList.remove("has-file");
            }
        });
    }

    // ── Alert helper ──
    function showAlert(msg, type) {
        uploadAlert.textContent = msg;
        uploadAlert.className = "form-alert " + type;
        uploadAlert.hidden = false;
        setTimeout(() => { uploadAlert.hidden = true; }, 5000);
    }

    // ── Upload form ──
    if (uploadForm) {
        uploadForm.addEventListener("submit", async (e) => {
            e.preventDefault();
            const submitBtn = uploadForm.querySelector(".resource-submit");
            const btnText = submitBtn.querySelector(".btn-text");
            const btnLoader = submitBtn.querySelector(".btn-loader");

            submitBtn.disabled = true;
            btnText.textContent = "Uploading...";
            if (btnLoader) btnLoader.hidden = false;

            const formData = new FormData();
            formData.append("title", document.getElementById("res-title").value.trim());
            formData.append("subject", document.getElementById("res-subject").value.trim());
            formData.append("branch", document.getElementById("res-branch").value);
            formData.append("year_of_engineering", document.getElementById("res-year").value);
            formData.append("academic_year", document.getElementById("res-academic-year").value.trim());
            formData.append("description", document.getElementById("res-description").value.trim());

            if (fileInput.files.length === 0) {
                showAlert("Please choose a PDF file.", "error");
                submitBtn.disabled = false;
                btnText.textContent = "Upload & Submit for Review";
                if (btnLoader) btnLoader.hidden = true;
                return;
            }
            formData.append("file", fileInput.files[0]);

            try {
                const res = await fetch("/api/resources/upload", { method: "POST", body: formData });
                const data = await res.json();
                if (!res.ok) {
                    showAlert(data.error || "Upload failed.", "error");
                } else {
                    showAlert(data.message || "Uploaded successfully!", "success");
                    uploadForm.reset();
                    fileNameDisplay.textContent = "No file chosen";
                    fileNameDisplay.classList.remove("has-file");
                    loadResources();
                    loadStats();
                }
            } catch (err) {
                showAlert("Network error. Please try again.", "error");
            } finally {
                submitBtn.disabled = false;
                btnText.textContent = "Upload & Submit for Review";
                if (btnLoader) btnLoader.hidden = true;
            }
        });
    }

    // ── Tabs ──
    tabs.forEach(tab => {
        tab.addEventListener("click", () => {
            tabs.forEach(t => t.classList.remove("active"));
            tab.classList.add("active");
            currentTab = tab.dataset.resTab;
            loadResources();
        });
    });

    // ── Filters ──
    let filterTimeout;
    function onFilterChange() {
        clearTimeout(filterTimeout);
        filterTimeout = setTimeout(() => loadResources(), 300);
    }
    if (filterBranch) filterBranch.addEventListener("change", onFilterChange);
    if (filterYear) filterYear.addEventListener("change", onFilterChange);
    if (filterSubject) filterSubject.addEventListener("input", onFilterChange);

    // ── Show loading state ──
    function showLoading() {
        if (!resourcesList) return;
        if (resourcesEmpty) resourcesEmpty.hidden = true;
        resourcesList.innerHTML = `
            <div class="resources-loading">
                <div class="loading-spinner"></div>
                <div class="loading-text">Loading resources...</div>
            </div>`;
    }

    // ── Load resources ──
    async function loadResources() {
        showLoading();
        
        let url = currentTab === "mine" ? "/api/resources/mine" : "/api/resources";
        const params = new URLSearchParams();

        if (currentTab === "all") {
            const branch = filterBranch ? filterBranch.value : "";
            const year = filterYear ? filterYear.value : "";
            const subject = filterSubject ? filterSubject.value.trim() : "";
            if (branch) params.set("branch", branch);
            if (year) params.set("year", year);
            if (subject) params.set("subject", subject);
        }

        const qs = params.toString();
        if (qs) url += "?" + qs;

        try {
            const res = await fetch(url);
            const data = await res.json();
            renderResources(data);
        } catch (err) {
            console.error("Failed to load resources:", err);
            if (resourcesList) {
                resourcesList.innerHTML = `
                    <div class="resources-empty">
                        <div class="empty-icon">
                            <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
                        </div>
                        <div class="empty-text">Failed to load resources</div>
                        <div class="empty-hint">Please check your connection and try again.</div>
                    </div>`;
            }
        }
    }

    // ── Render resources ──
    function renderResources(resources) {
        if (!resourcesList) return;

        if (!resources || resources.length === 0) {
            resourcesList.innerHTML = "";
            if (resourcesEmpty) resourcesEmpty.hidden = false;
            return;
        }
        if (resourcesEmpty) resourcesEmpty.hidden = true;

        resourcesList.innerHTML = resources.map(r => {
            const isMine = currentTab === "mine";
            const statusHtml = isMine ? `
                <span class="status-badge status-${esc(r.status)}">
                    <span class="status-dot"></span>
                    ${esc(r.status)}
                </span>` : "";

            const deleteBtn = isMine ? `
                <button class="btn-delete-res" onclick="window.__deleteResource(${r.id})">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                    Delete
                </button>` : "";

            const editBtn = isMine ? `
                <button class="btn-edit-res" onclick="window.__openEditResource(${r.id})">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
                    Edit
                </button>` : "";

            const commentsBtn = isMine ? `
                <button class="btn-comments-res" onclick="window.__toggleUserComments(${r.id})">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
                    Comments
                </button>` : "";

            const downloadUrl = `/api/resources/${r.id}/download`;
            const escapedTitle = esc(r.title).replace(/'/g, "\\'");

            return `
            <div class="resource-item">
                <div class="resource-icon">
                    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></svg>
                </div>
                <div class="resource-info">
                    <div class="resource-title">${esc(r.title)}</div>
                    <div class="resource-subject">${esc(r.subject)}</div>
                    <div class="resource-meta">
                        <span class="resource-tag tag-branch">${esc(r.branch)}</span>
                        <span class="resource-tag tag-year">${esc(r.year_of_engineering)}</span>
                        <span class="resource-tag tag-academic">${esc(r.academic_year)}</span>
                    </div>
                    <div class="resource-uploader">Uploaded by ${esc(r.uploader_name)} · ${formatDate(r.uploaded_at)}</div>
                </div>
                <div class="resource-actions">
                    ${statusHtml}
                    <button class="btn-preview" onclick="window.__previewResource(${r.id}, '${escapedTitle}')">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
                        Preview
                    </button>
                    <button class="btn-ai-refine" onclick="window.__openAiRefine(${r.id}, '${escapedTitle}', '${esc(r.subject).replace(/'/g, "\\'")}')">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg>
                        AI Refine
                    </button>
                    <a href="${downloadUrl}" class="btn-download" target="_blank">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                        Download
                    </a>
                    ${editBtn}
                    ${commentsBtn}
                    ${deleteBtn}
                </div>
                ${isMine ? `<div class="resource-comments-section" id="user-comments-${r.id}" hidden></div>` : ""}
            </div>`;
        }).join("");
    }

    // ── PDF Preview ──
    window.__previewResource = function (id, title) {
        const modal = document.getElementById('pdfPreviewModal');
        const frame = document.getElementById('pdfModalFrame');
        const titleEl = document.getElementById('pdfModalTitle');
        if (!modal || !frame) return;
        if (titleEl) titleEl.textContent = title || 'Preview';
        frame.src = `/api/resources/${id}/download?preview=1`;
        modal.hidden = false;
        document.body.style.overflow = 'hidden';
    };

    window.__closePdfPreview = function () {
        const modal = document.getElementById('pdfPreviewModal');
        const frame = document.getElementById('pdfModalFrame');
        if (modal) modal.hidden = true;
        if (frame) frame.src = '';
        document.body.style.overflow = '';
    };

    // close on Escape
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') {
            window.__closePdfPreview();
            window.__closeEditModal();
            window.__closeAiRefine();
        }
    });

    // ── Delete ──
    window.__deleteResource = async function (id) {
        if (!confirm("Delete this resource?")) return;
        try {
            const res = await fetch(`/api/resources/${id}`, { method: "DELETE" });
            if (res.ok) {
                loadResources();
                loadStats();
            }
        } catch (err) {
            console.error("Delete failed:", err);
        }
    };

    // ── Edit resource ──
    window.__openEditResource = async function (id) {
        try {
            const res = await fetch("/api/resources/mine");
            const mine = await res.json();
            const r = mine.find(x => x.id === id);
            if (!r) return alert("Resource not found.");

            document.getElementById("edit-res-id").value = r.id;
            document.getElementById("edit-res-title").value = r.title || "";
            document.getElementById("edit-res-subject").value = r.subject || "";
            document.getElementById("edit-res-branch").value = r.branch || "";
            document.getElementById("edit-res-year").value = r.year_of_engineering || "";
            document.getElementById("edit-res-academic-year").value = r.academic_year || "";
            document.getElementById("edit-res-description").value = r.description || "";
            document.getElementById("edit-file-name").textContent = "Keep current file (optional re-upload)";
            document.getElementById("edit-file-name").classList.remove("has-file");
            document.getElementById("edit-res-file").value = "";

            const modal = document.getElementById("editResourceModal");
            if (modal) { modal.hidden = false; document.body.style.overflow = "hidden"; }
        } catch (err) { console.error("Edit open failed:", err); }
    };

    window.__closeEditModal = function () {
        const modal = document.getElementById("editResourceModal");
        if (modal) { modal.hidden = true; document.body.style.overflow = ""; }
    };

    // Edit form submission
    const editForm = document.querySelector("[data-edit-form]");
    const editFileInput = document.getElementById("edit-res-file");
    const editFilePickBtn = document.querySelector("[data-edit-file-pick]");
    const editFileName = document.getElementById("edit-file-name");

    if (editFilePickBtn && editFileInput) {
        editFilePickBtn.addEventListener("click", () => editFileInput.click());
        editFileInput.addEventListener("change", () => {
            if (editFileInput.files.length > 0) {
                editFileName.textContent = editFileInput.files[0].name;
                editFileName.classList.add("has-file");
            } else {
                editFileName.textContent = "Keep current file (optional re-upload)";
                editFileName.classList.remove("has-file");
            }
        });
    }

    if (editForm) {
        editForm.addEventListener("submit", async (e) => {
            e.preventDefault();
            const id = document.getElementById("edit-res-id").value;
            const submitBtn = editForm.querySelector(".edit-submit-btn");
            const btnText = submitBtn.querySelector(".btn-text");
            submitBtn.disabled = true;
            btnText.textContent = "Saving...";

            const formData = new FormData();
            formData.append("title", document.getElementById("edit-res-title").value.trim());
            formData.append("subject", document.getElementById("edit-res-subject").value.trim());
            formData.append("branch", document.getElementById("edit-res-branch").value);
            formData.append("year_of_engineering", document.getElementById("edit-res-year").value);
            formData.append("academic_year", document.getElementById("edit-res-academic-year").value.trim());
            formData.append("description", document.getElementById("edit-res-description").value.trim());
            if (editFileInput.files.length > 0) {
                formData.append("file", editFileInput.files[0]);
            }

            try {
                const res = await fetch(`/api/resources/${id}`, { method: "PUT", body: formData });
                const data = await res.json();
                if (!res.ok) {
                    alert(data.error || "Update failed.");
                } else {
                    window.__closeEditModal();
                    loadResources();
                    loadStats();
                }
            } catch (err) { alert("Network error."); }
            finally {
                submitBtn.disabled = false;
                btnText.textContent = "Save Changes";
            }
        });
    }

    // ── Comments for user's resources ──
    window.__toggleUserComments = async function (id) {
        const wrap = document.getElementById(`user-comments-${id}`);
        if (!wrap) return;
        if (wrap.hidden) {
            wrap.hidden = false;
            await __loadUserComments(id);
        } else {
            wrap.hidden = true;
        }
    };

    async function __loadUserComments(id) {
        const wrap = document.getElementById(`user-comments-${id}`);
        if (!wrap) return;
        try {
            const res = await fetch(`/api/resources/${id}/comments`);
            const comments = await res.json();
            if (!comments || comments.length === 0) {
                wrap.innerHTML = '<div class="comments-empty">No comments yet.</div>';
                return;
            }
            wrap.innerHTML = comments.map(c => {
                const isAdmin = c.is_admin;
                return `<div class="comment-bubble ${isAdmin ? 'comment-admin' : 'comment-user'}">
                    <div class="comment-header">${esc(c.commenter_name)} ${isAdmin ? '(Admin)' : ''} · ${formatDate(c.created_at)}</div>
                    <div class="comment-text">${esc(c.comment)}</div>
                </div>`;
            }).join('');
        } catch (err) { console.error("Load comments failed:", err); }
    }

    // ── Stats ──
    async function loadStats() {
        try {
            const [allRes, myRes] = await Promise.all([
                fetch("/api/resources").then(r => r.json()),
                fetch("/api/resources/mine").then(r => r.json()),
            ]);
            if (statTotal) statTotal.textContent = allRes.length;
            if (statMy) statMy.textContent = myRes.length;
            if (statPending) {
                const pending = myRes.filter(r => r.status === "pending").length;
                statPending.textContent = pending;
            }
        } catch (err) {
            console.error("Stats load failed:", err);
        }
    }

    // ── Utilities ──
    function esc(str) {
        if (!str) return "";
        return String(str).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
    }

    function formatDate(dateStr) {
        if (!dateStr) return "";
        try {
            const d = new Date(dateStr);
            return d.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
        } catch { return dateStr; }
    }

    // ═══════════════════════════════════════════════════════════════════════════
    // AI REFINE FEATURE
    // ═══════════════════════════════════════════════════════════════════════════

    let currentRefineResourceId = null;
    let currentRefineResourceTitle = '';
    let currentRefineResourceSubject = '';

    function setAiRefineState(state) {
        const contextState = document.getElementById('aiRefineContextState');
        const loadingState = document.getElementById('aiRefineLoading');
        const resultState = document.getElementById('aiRefineResult');
        const errorState = document.getElementById('aiRefineError');

        if (contextState) contextState.hidden = state !== 'context';
        if (loadingState) loadingState.hidden = state !== 'loading';
        if (resultState) resultState.hidden = state !== 'result';
        if (errorState) errorState.hidden = state !== 'error';
    }

    function getAiRefineContextPayload() {
        const collegeName = (document.getElementById('ai-college-name')?.value || '').trim();
        const affiliatedTo = (document.getElementById('ai-affiliated-to')?.value || '').trim();
        const coPo = (document.getElementById('ai-co-po')?.value || '').trim();
        const syllabusContext = (document.getElementById('ai-syllabus-context')?.value || '').trim();
        const universityRegulation = (document.getElementById('ai-university-regulation')?.value || '').trim();

        return {
            college_name: collegeName,
            affiliated_to: affiliatedTo,
            course_outcomes_program_outcomes: coPo,
            syllabus_context: syllabusContext,
            university_regulation: universityRegulation,
        };
    }

    async function runAiRefinement(resourceId, payload) {
        const refineRes = await fetch(`/api/resources/${resourceId}/refine`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const refineData = await refineRes.json();

        if (!refineRes.ok) {
            throw new Error(refineData.error || 'Failed to start AI refinement');
        }

        const resultRes = await fetch(`/api/resources/${resourceId}/refinement`);
        const resultData = await resultRes.json();
        if (!resultRes.ok || !resultData.exists) {
            throw new Error('Refinement completed but failed to load results.');
        }
        return resultData;
    }

    window.__openAiRefine = async function (resourceId, title, subject) {
        currentRefineResourceId = resourceId;
        currentRefineResourceTitle = title || 'Document';
        currentRefineResourceSubject = subject || '';
        const modal = document.getElementById('aiRefineModal');
        if (!modal) return;

        // Set title
        const titleEl = document.getElementById('aiRefineTitle');
        if (titleEl) titleEl.textContent = title;

        const contextForm = document.getElementById('aiRefineContextForm');
        const contextError = document.getElementById('aiRefineContextError');
        const submitBtn = document.getElementById('aiContextSubmitBtn');

        if (contextForm) {
            contextForm.reset();
        }
        if (contextError) {
            contextError.hidden = true;
            contextError.textContent = '';
        }
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.textContent = 'Continue and Refine Notes';
        }

        // Start from context form instead of direct refinement.
        setAiRefineState('context');

        // Reset loading steps
        const steps = document.querySelectorAll('.ai-step');
        steps.forEach((step) => {
            step.classList.add('active');
        });

        // Show modal
        modal.hidden = false;
        document.body.style.overflow = 'hidden';

    };

    const aiRefineContextForm = document.getElementById('aiRefineContextForm');
    if (aiRefineContextForm) {
        aiRefineContextForm.addEventListener('submit', async (event) => {
            event.preventDefault();
            if (!currentRefineResourceId) return;

            const contextError = document.getElementById('aiRefineContextError');
            const submitBtn = document.getElementById('aiContextSubmitBtn');
            const payload = getAiRefineContextPayload();

            if (!payload.syllabus_context) {
                if (contextError) {
                    contextError.textContent = 'Syllabus Context is mandatory.';
                    contextError.hidden = false;
                }
                return;
            }

            if (contextError) {
                contextError.hidden = true;
                contextError.textContent = '';
            }

            if (submitBtn) {
                submitBtn.disabled = true;
                submitBtn.textContent = 'Refining...';
            }

            // Reset loading steps
            const steps = document.querySelectorAll('.ai-step');
            steps.forEach((step) => step.classList.add('active'));

            setAiRefineState('loading');
            try {
                const resultData = await runAiRefinement(currentRefineResourceId, payload);
                displayRefinementResult(resultData);
            } catch (err) {
                showRefineError(err.message || 'Network error. Please try again.');
            } finally {
                if (submitBtn) {
                    submitBtn.disabled = false;
                    submitBtn.textContent = 'Continue and Refine Notes';
                }
            }
        });
    }

    function displayRefinementResult(data) {
        const loadingState = document.getElementById('aiRefineLoading');
        const resultState = document.getElementById('aiRefineResult');
        const errorState = document.getElementById('aiRefineError');

        setAiRefineState('result');
        if (loadingState) loadingState.hidden = true;
        if (errorState) errorState.hidden = true;
        if (resultState) resultState.hidden = false;

        // Display summary
        const summaryEl = document.getElementById('aiRefineSummary');
        if (summaryEl && data.summary) {
            summaryEl.innerHTML = formatMarkdown(data.summary);
        }

        // Display Q&A with mind maps
        const qaContainer = document.getElementById('aiRefineQA');
        if (qaContainer && data.questions) {
            qaContainer.innerHTML = data.questions.map((q, idx) => `
                <div class="qa-item" data-qa-id="${q.id || idx}">
                    <div class="qa-question">
                        <span class="qa-number">Q${idx + 1}</span>
                        <span class="qa-text">${esc(q.question)}</span>
                    </div>
                    <div class="qa-answer">
                        <div class="qa-answer-label">Answer:</div>
                        <div class="qa-answer-text">${formatMarkdown(q.answer)}</div>
                    </div>
                    <div class="qa-mindmap-section">
                        <button class="btn-show-mindmap" onclick="window.__toggleMindMap(${idx})">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 8v4l2 2"/></svg>
                            View Mind Map
                        </button>
                        <div class="qa-mindmap" id="mindmap-${idx}" hidden>
                            <div class="mermaid-container" id="mermaid-${idx}"></div>
                        </div>
                    </div>
                </div>
            `).join('');

            // Store mindmap data for rendering
            window.__mindmapData = data.questions.map(q => q.mindmap || '');
        }
    }

    window.__toggleMindMap = function (idx) {
        const container = document.getElementById(`mindmap-${idx}`);
        const mermaidContainer = document.getElementById(`mermaid-${idx}`);
        
        if (!container) return;

        if (container.hidden) {
            container.hidden = false;
            
            // Render mermaid diagram if not already rendered
            if (mermaidContainer && !mermaidContainer.dataset.rendered) {
                const mindmapCode = window.__mindmapData && window.__mindmapData[idx];
                if (mindmapCode) {
                    try {
                        // Clean up the mindmap code
                        let cleanCode = mindmapCode.replace(/\\n/g, '\n').trim();
                        if (!cleanCode.startsWith('mindmap')) {
                            cleanCode = 'mindmap\n  root((Concept))\n    ' + cleanCode;
                        }
                        
                        mermaidContainer.innerHTML = `<pre class="mermaid">${cleanCode}</pre>`;
                        
                        // Re-run mermaid
                        if (window.mermaid) {
                            window.mermaid.init(undefined, mermaidContainer.querySelector('.mermaid'));
                        }
                        mermaidContainer.dataset.rendered = 'true';
                    } catch (err) {
                        mermaidContainer.innerHTML = `<div class="mindmap-error">Unable to render mind map</div>`;
                    }
                } else {
                    mermaidContainer.innerHTML = `<div class="mindmap-placeholder">Mind map not available</div>`;
                }
            }
        } else {
            container.hidden = true;
        }
    };

    function showRefineError(message) {
        const loadingState = document.getElementById('aiRefineLoading');
        const resultState = document.getElementById('aiRefineResult');
        const errorState = document.getElementById('aiRefineError');
        const errorMsg = document.getElementById('aiRefineErrorMsg');

        setAiRefineState('error');
        if (loadingState) loadingState.hidden = true;
        if (resultState) resultState.hidden = true;
        if (errorState) errorState.hidden = false;
        if (errorMsg) errorMsg.textContent = message;
    }

    window.__closeAiRefine = function () {
        const modal = document.getElementById('aiRefineModal');
        if (modal) modal.hidden = true;
        document.body.style.overflow = '';
    };

    window.__retryAiRefine = async function () {
        if (currentRefineResourceId) {
            window.__openAiRefine(currentRefineResourceId, currentRefineResourceTitle, currentRefineResourceSubject);
        }
    };

    // Simple markdown formatter
    function formatMarkdown(text) {
        if (!text) return '';
        return text
            .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
            .replace(/\*(.+?)\*/g, '<em>$1</em>')
            .replace(/^### (.+)$/gm, '<h4>$1</h4>')
            .replace(/^## (.+)$/gm, '<h3>$1</h3>')
            .replace(/^# (.+)$/gm, '<h2>$1</h2>')
            .replace(/^- (.+)$/gm, '<li>$1</li>')
            .replace(/(<li>.*<\/li>)/s, '<ul>$1</ul>')
            .replace(/\n\n/g, '</p><p>')
            .replace(/\n/g, '<br>');
    }

    // ── Init ──
    loadResources();
    loadStats();

    // Initialize Mermaid if available
    if (window.mermaid) {
        window.mermaid.initialize({
            startOnLoad: false,
            theme: 'dark',
            themeVariables: {
                primaryColor: '#FF6B35',
                primaryTextColor: '#fff',
                primaryBorderColor: '#FF6B35',
                lineColor: '#63e6d3',
                secondaryColor: '#1a1a2e',
                tertiaryColor: '#0f0f23'
            }
        });
    }
})();
