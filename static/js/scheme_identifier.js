(function () {
    "use strict";

    const form = document.getElementById("yt-mindmap-form");
    const urlInput = document.getElementById("youtube-url");
    const alertBox = document.getElementById("yt-alert");
    const generateBtn = document.getElementById("generate-mindmap-btn");

    const metaVideoTitle = document.getElementById("meta-video-title");
    const metaCharCount = document.getElementById("meta-char-count");

    const previewWrap = document.getElementById("yt-preview-wrap");
    const codeWrap = document.getElementById("yt-code-wrap");
    const emptyState = document.getElementById("yt-empty-state");

    const mindmapImage = document.getElementById("mindmap-image");
    const openSvgLink = document.getElementById("open-svg-link");
    const openEditorLink = document.getElementById("open-editor-link");
    const codeBlock = document.getElementById("mermaid-code");
    const copyBtn = document.getElementById("copy-mermaid-btn");

    function showAlert(message, type) {
        if (!alertBox) return;
        alertBox.textContent = message;
        alertBox.className = "form-alert " + type;
        alertBox.hidden = false;
        setTimeout(() => {
            alertBox.hidden = true;
        }, 5000);
    }

    function setLoading(loading) {
        if (!generateBtn) return;
        const btnText = generateBtn.querySelector(".btn-text");
        const btnLoader = generateBtn.querySelector(".btn-loader");

        generateBtn.disabled = loading;
        if (btnText) btnText.textContent = loading ? "Generating..." : "Generate Mindmap";
        if (btnLoader) btnLoader.hidden = !loading;
    }

    function renderResult(data) {
        if (metaVideoTitle) metaVideoTitle.textContent = data.videoTitle || "YouTube Video";
        if (metaCharCount) metaCharCount.textContent = String(data.charCount || 0);

        if (mindmapImage) {
            mindmapImage.src = data.imageUrl;
            mindmapImage.alt = (data.videoTitle || "Video") + " mindmap";
        }
        if (openSvgLink) openSvgLink.href = data.svgUrl;
        if (openEditorLink) openEditorLink.href = data.editorUrl;
        if (codeBlock) codeBlock.textContent = data.mermaidCode || "";

        if (previewWrap) previewWrap.hidden = false;
        if (codeWrap) codeWrap.hidden = false;
        if (emptyState) emptyState.hidden = true;
    }

    async function handleSubmit(event) {
        event.preventDefault();

        const youtubeUrl = (urlInput.value || "").trim();
        if (!youtubeUrl) {
            showAlert("Please enter a YouTube URL.", "error");
            return;
        }

        setLoading(true);

        try {
            const response = await fetch("/api/youtube-mindmap/generate", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ youtube_url: youtubeUrl }),
            });

            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.error || "Failed to generate mindmap.");
            }

            renderResult(data);
            showAlert("Mindmap generated successfully.", "success");
        } catch (error) {
            showAlert(error.message || "Failed to generate mindmap.", "error");
        } finally {
            setLoading(false);
        }
    }

    async function copyCode() {
        const text = codeBlock ? codeBlock.textContent : "";
        if (!text) {
            showAlert("No Mermaid code to copy yet.", "error");
            return;
        }
        try {
            await navigator.clipboard.writeText(text);
            showAlert("Mermaid code copied.", "success");
        } catch (error) {
            showAlert("Copy failed. Please copy manually.", "error");
        }
    }

    if (form) {
        form.addEventListener("submit", handleSubmit);
    }

    if (copyBtn) {
        copyBtn.addEventListener("click", copyCode);
    }
})();
