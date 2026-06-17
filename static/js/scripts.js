// ===========================================================================
// Board context. The page is served for either the default/public board
// (data-board="") or a named board (data-board="<slug>"). Every API call is
// scoped through api() so the same code drives both.
// ===========================================================================
const BOARD = (document.body.dataset.board || '').trim();
let BOARD_PERM = BOARD ? 'private' : 'public';
let BOARD_AUTHED = false;
let BOARD_LOCKED = false;       // private board we cannot currently read
let BOARD_DISPLAY = BOARD;
let BOARD_RETENTION = '';

function api(suffix) {
	return BOARD ? ('/b/' + BOARD + suffix) : suffix;
}

// Mirror of canonical_slug() in boards.py — keep the two in sync.
function canonicalizeSlug(raw) {
	if (!raw) return '';
	let s = String(raw).trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
	s = s.slice(0, 64).replace(/^-+|-+$/g, '');
	if (!s || s === 'default') return '';
	return s;
}

function canPost()   { return BOARD_AUTHED || ['append', 'public'].includes(BOARD_PERM); }
function canDelete() { return BOARD_AUTHED || BOARD_PERM === 'public'; }

function esc(s) {
	const d = document.createElement('div');
	d.textContent = s == null ? '' : String(s);
	return d.innerHTML;
}

// ===========================================================================
// Live updates (poll the board's own update clock).
// ===========================================================================
let lastKnownUpdate = 0;
let pollTimer = null;

function stopPolling() {
	if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}

async function checkForUpdates() {
	let response;
	try {
		response = await fetch(api('/last-update'));
	} catch (e) {
		return;
	}
	if (response.status === 401) {   // private board, not authed
		BOARD_LOCKED = true;
		stopPolling();               // don't keep hammering a board we can't read
		renderLocked();
		return;
	}
	if (!response.ok) return;
	const data = await response.json();
	if (data.last_update > lastKnownUpdate) {
		lastKnownUpdate = data.last_update;
		fetchMessages();
		pulseLive();
	}
}

function pulseLive() {
	const el = document.getElementById('liveDot');
	if (!el) return;
	el.classList.remove('pulse');
	void el.offsetWidth;
	el.classList.add('pulse');
}

pollTimer = setInterval(checkForUpdates, 5000);

// ===========================================================================
// Compose / upload
// ===========================================================================
document.getElementById('messageForm').addEventListener('submit', async function(e) {
	e.preventDefault();
	const message = document.getElementById('message').value;
	const formData = new FormData(this);
	formData.append('message', message);

	const xhr = new XMLHttpRequest();
	xhr.open('POST', api('/message'), true);

	const progress = document.getElementById('progress');
	progress.classList.add('active');

	xhr.upload.addEventListener('progress', function(e) {
		if (e.lengthComputable) {
			const percentComplete = (e.loaded / e.total) * 100;
			const uploadProgress = document.getElementById('uploadProgress');
			uploadProgress.textContent = `${percentComplete.toFixed(0)}%`;
			uploadProgress.className = 'uploading';
			document.getElementById('progressBar').style.width = `${percentComplete}%`;
		}
	});

	xhr.onload = function() {
		const uploadProgress = document.getElementById('uploadProgress');
		if (xhr.status === 200) {
			document.getElementById('messageForm').reset();
			document.getElementById('message').value = '';
			document.getElementById('image-name').textContent = '';
			document.getElementById('video-name').textContent = '';
			document.getElementById('file-name').textContent = '';
			uploadProgress.textContent = 'Sent';
			uploadProgress.className = 'upload-complete';
			document.getElementById('progressBar').style.width = '100%';
			checkForUpdates();
		} else if (xhr.status === 401) {
			uploadProgress.textContent = 'Login required';
			uploadProgress.className = 'upload-failed';
			document.getElementById('progressBar').style.width = '0%';
			openLoginModal(BOARD, BOARD_DISPLAY);
		} else {
			uploadProgress.textContent = 'Upload failed';
			uploadProgress.className = 'upload-failed';
			document.getElementById('progressBar').style.width = '0%';
		}
		setTimeout(function() {
			progress.classList.remove('active');
			document.getElementById('progressBar').style.width = '0%';
			uploadProgress.textContent = '';
		}, 1500);
	};

	xhr.send(formData);
});

document.getElementById('clearButton').addEventListener('click', function() {
	document.getElementById('messageForm').reset();
	document.getElementById('image-name').textContent = '';
	document.getElementById('video-name').textContent = '';
	document.getElementById('file-name').textContent = '';
});

// ===========================================================================
// Rendering helpers (HTML / Markdown / plain)
// ===========================================================================
function isHTML(str) {
	const doc = new DOMParser().parseFromString(str, "text/html");
	if (doc.querySelector('parsererror')) {
		return false;
	}
	const hasElementNodes = (node) => node.nodeType === 1 && node.tagName.toLowerCase() !== 'html';
	const bodyHasNodes = Array.from(doc.body.childNodes).some(hasElementNodes);
	const headHasNodes = Array.from(doc.head.childNodes).some(hasElementNodes);
	return bodyHasNodes || headHasNodes;
}

function looksLikeMarkdown(str) {
	if (!str || !str.trim()) {
		return false;
	}
	const patterns = [
		/^#{1,6}\s+\S/m,
		/^\s*[-*+]\s+\S/m,
		/^\s*\d+\.\s+\S/m,
		/^\s*>\s+\S/m,
		/```[\s\S]*?```/,
		/`[^`\n]+`/,
		/\*\*[^*\n]+\*\*/,
		/__[^_\n]+__/,
		/\[[^\]]+\]\([^)\s]+\)/,
		/^\s*\|.+\|\s*$/m,
		/^(\s*)(-{3,}|\*{3,}|_{3,})\s*$/m,
		/^\S.*\n(={3,}|-{3,})\s*$/m,
	];
	return patterns.some((re) => re.test(str));
}

function buildTextElement(content, mode) {
	if (mode === 'html') {
		const div = document.createElement('div');
		div.innerHTML = DOMPurify.sanitize(content);
		return div;
	}
	if (mode === 'markdown') {
		const div = document.createElement('div');
		div.classList.add('markdown-body');
		div.innerHTML = DOMPurify.sanitize(marked.parse(content));
		return div;
	}
	const pre = document.createElement('pre');
	pre.textContent = content;
	return pre;
}

function detectTextMode(content) {
	if (isHTML(content)) {
		return 'html';
	}
	if (looksLikeMarkdown(content)) {
		return 'markdown';
	}
	return 'plain';
}

async function fetchMessages() {
    let response;
    try {
        response = await fetch(api('/messages'));
    } catch (e) {
        return;
    }
    if (response.status === 401) {
        BOARD_LOCKED = true;
        stopPolling();
        renderLocked();
        return;
    }
    if (!response.ok) return;
    const result = await response.json();

    // Refresh board state from the response and re-skin the UI accordingly.
    BOARD_LOCKED = false;
    if (BOARD) {
        BOARD_PERM = result.perm || BOARD_PERM;
        BOARD_AUTHED = !!result.authed;
        BOARD_DISPLAY = result.display || BOARD;
        BOARD_RETENTION = result.retention || '';
    }
    applyPermUI();

    const messagesDiv = document.getElementById('messages');
    messagesDiv.innerHTML = '';

    result.messages.forEach((message) => {
        const messageElement = document.createElement('div');
        messageElement.classList.add('message');
        messageElement.id = `message-${message.id}`;

        let contentToCopy = null;
        let contentElementRef = null;

        const contentContainer = document.createElement('div');
        contentContainer.classList.add('content-container');

        const textMode = message.type === 'text' ? detectTextMode(message.content) : 'plain';

        if (message.type === 'text') {
            contentElementRef = buildTextElement(message.content, textMode);
            contentContainer.appendChild(contentElementRef);
            contentToCopy = contentElementRef;

        } else if (message.type === 'image') {
            if (message.filename && message.filename !== 'image.png') {
                const imgName = document.createElement('p');
                imgName.textContent = message.filename;
                contentContainer.appendChild(imgName);
            }
            const img = document.createElement('img');
            img.src = message.content;
            img.style.maxWidth = '100%';
            contentContainer.appendChild(img);
            contentElementRef = img;
            contentToCopy = img;

        } else if (message.type === 'video') {
            if (message.filename) {
                const videoName = document.createElement('p');
                videoName.textContent = message.filename;
                contentContainer.appendChild(videoName);
            }
            const video = document.createElement('video');
            video.src = message.content;
            video.controls = true;
            video.style.maxWidth = '100%';
            contentContainer.appendChild(video);
            contentElementRef = video;
            contentToCopy = video;

        } else if (message.type === 'file') {
            const a = document.createElement('a');
            a.href = message.content;
            a.textContent = message.filename || 'Download File';
            a.download = '';
            contentContainer.appendChild(a);
            contentElementRef = a;
            contentToCopy = a;

        } else {
            console.error('Unknown message type:', message.type);
            const pre = document.createElement('pre');
            pre.textContent = 'Unknown message type';
            contentContainer.appendChild(pre);
            contentElementRef = pre;
            contentToCopy = pre;
        }

        messageElement.appendChild(contentContainer);

        const meta = document.createElement('div');
        meta.classList.add('message-meta');
        const typeTag = document.createElement('span');
        typeTag.classList.add('msg-type');
        typeTag.textContent = message.type;
        const timeTag = document.createElement('span');
        timeTag.classList.add('msg-time');
        const date = new Date(message.timestamp * 1000);
        timeTag.textContent = date.toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
        meta.appendChild(typeTag);
        meta.appendChild(timeTag);
        messageElement.insertBefore(meta, messageElement.firstChild);

        const buttonsContainer = document.createElement('div');
        buttonsContainer.classList.add('buttons-container');

        const copyButton = document.createElement('button');
        copyButton.textContent = 'Copy';
        copyButton.classList.add('copy-button');
        copyButton.onclick = function() { copyToClipboard(contentToCopy); };
        buttonsContainer.appendChild(copyButton);

        // Only offer delete where the viewer is actually allowed to delete.
        if (canDelete()) {
            const deleteButton = document.createElement('button');
            deleteButton.textContent = 'Delete';
            deleteButton.classList.add('delete-button');
            deleteButton.onclick = function() { deleteMessage(message.id); };
            buttonsContainer.appendChild(deleteButton);
        }

        if (textMode !== 'plain') {
            const showRawButton = document.createElement('button');
            showRawButton.textContent = 'Show Raw';
            showRawButton.classList.add('show-raw-button');
            messageElement.setAttribute('data-show-raw', 'false');

            showRawButton.onclick = function() {
                const isCurrentlyRaw = (messageElement.getAttribute('data-show-raw') === 'true');
                const newMode = isCurrentlyRaw ? textMode : 'plain';
                const newElement = buildTextElement(message.content, newMode);
                contentContainer.replaceChild(newElement, contentElementRef);
                contentElementRef = newElement;
                contentToCopy = newElement;
                messageElement.setAttribute('data-show-raw', isCurrentlyRaw ? 'false' : 'true');
                showRawButton.textContent = isCurrentlyRaw ? 'Show Raw' : 'Show Rendered';
            };

            buttonsContainer.appendChild(showRawButton);
        }

        messageElement.appendChild(buttonsContainer);
        messagesDiv.appendChild(messageElement);
    });
}

// ===========================================================================
// Clipboard
// ===========================================================================
function copyToClipboard(element) {
	if (!element) {
		showToast('No content to copy!');
		return;
	}
	if (element.tagName === 'IMG') {
		if (typeof ClipboardItem !== "undefined") {
			fetch(element.src)
				.then(res => res.blob())
				.then(blob => {
					if (blob.type === "image/png") {
						const item = new ClipboardItem({ "image/png": blob });
						navigator.clipboard.write([item]).then(() => {
							showToast('Image copied to clipboard!');
						}, (err) => {
							showToast('Failed to copy image: ' + err);
						});
					} else {
						const canvas = document.createElement('canvas');
						const ctx = canvas.getContext('2d');
						const img = new Image();
						img.onload = () => {
							canvas.width = img.width;
							canvas.height = img.height;
							ctx.drawImage(img, 0, 0);
							canvas.toBlob((pngBlob) => {
								const item = new ClipboardItem({ "image/png": pngBlob });
								navigator.clipboard.write([item]).then(() => {
									showToast('Converted Image copied to clipboard!');
								}, (err) => {
									showToast('Failed to copy image: ' + err);
								});
							}, 'image/png');
						};
						img.src = URL.createObjectURL(blob);
					}
				});
		} else {
			navigator.clipboard.writeText(element.src).then(() => {
				showToast('Image URL copied to clipboard!');
			}, (err) => {
				showToast('Failed to copy image URL: ' + err);
			});
		}
	} else if (element.tagName === 'PRE' || element.tagName === 'P') {
		navigator.clipboard.writeText(element.textContent).then(() => {
			showToast('Text copied to clipboard!');
		}, (err) => {
			showToast('Failed to copy text: ' + err);
		});
	} else if (element.tagName === 'DIV') {
		const selection = window.getSelection();
		const range = document.createRange();
		range.selectNodeContents(element);
		selection.removeAllRanges();
		selection.addRange(range);
		document.execCommand('copy');
		selection.removeAllRanges();
		showToast('HTML copied to clipboard!');
	} else if (element.tagName === 'A') {
		navigator.clipboard.writeText(element.href).then(() => {
			showToast('Link copied to clipboard!');
		}, (err) => {
			showToast('Failed to copy link: ' + err);
		});
	} else if (element.tagName === 'VIDEO') {
		navigator.clipboard.writeText(element.src).then(() => {
			showToast('Video URL copied to clipboard!');
		}, (err) => {
			showToast('Failed to copy video URL: ' + err);
		});
	} else {
		showToast('Unsupported content!');
	}
}

// True when focus is in any editable field (the compose box, the board name
// box, a TOTP/code input, etc.) — those should keep native copy/paste.
function inEditableField() {
	const ae = document.activeElement;
	return !!ae && (ae.tagName === 'INPUT' || ae.tagName === 'TEXTAREA' || ae.isContentEditable);
}

document.addEventListener('copy', function(e) {
	if (inEditableField()) {
		return;
	}
	const selection = window.getSelection();
	if (!selection.toString().trim()) {
		e.preventDefault();
		const newestMessage = document.querySelector('.message');
		if (newestMessage) {
			const contentElement = newestMessage.querySelector('pre')
				|| newestMessage.querySelector('img')
				|| newestMessage.querySelector('div');
			if (contentElement) {
				copyToClipboard(contentElement);
			}
		}
	}
});

function showToast(message) {
	const toast = document.createElement('div');
	toast.textContent = message;
	toast.className = 'toast';
	document.body.appendChild(toast);
	setTimeout(() => {
		document.body.removeChild(toast);
	}, 3000);
}

// ===========================================================================
// Paste / drag-drop (respect post permission; prompt login when blocked)
// ===========================================================================
function postFormData(formData) {
	fetch(api('/message'), { method: 'POST', body: formData })
		.then(response => {
			if (response.status === 401) {
				openLoginModal(BOARD, BOARD_DISPLAY);
				return null;
			}
			return response.json();
		})
		.then(result => { if (result) checkForUpdates(); })
		.catch(error => console.error('Error:', error));
}

document.body.addEventListener('paste', async function(e) {
	if (inEditableField()) {
		return;   // let the focused field (compose, board name, TOTP code…) paste normally
	}
	if (!canPost()) return;
	e.preventDefault();
	const items = e.clipboardData.items;
	for (const item of items) {
		if (item.kind === 'string') {
			item.getAsString((text) => {
				const formData = new FormData();
				formData.append('message', text);
				postFormData(formData);
			});
		} else if (item.kind === 'file' && item.type.startsWith('image/')) {
			const file = item.getAsFile();
			const formData = new FormData();
			formData.append('image', file);
			formData.append('message', '');
			postFormData(formData);
		}
	}
});

document.body.addEventListener('dragover', function(e) {
	e.preventDefault();
	e.stopPropagation();
	e.target.style.background = '#f0f0f0';
});

document.body.addEventListener('dragenter', function(e) {
	e.preventDefault();
	e.stopPropagation();
	e.target.style.background = '#e0e0e0';
});

document.body.addEventListener('dragleave', function(e) {
	e.preventDefault();
	e.stopPropagation();
	e.target.style.background = '';
});

document.body.addEventListener('drop', function(e) {
	e.preventDefault();
	e.stopPropagation();
	e.target.style.background = '';
	if (!canPost()) { showToast('This board is read-only.'); return; }
	const files = e.dataTransfer.files;
	const formData = new FormData();
	for (const file of files) {
		if (file.type.startsWith('image/')) {
			formData.append('image', file);
		}
	}
	formData.append('message', '');
	postFormData(formData);
});

async function deleteMessage(messageIndex) {
	const r = await fetch(api(`/delete/${messageIndex}`), { method: 'POST' });
	if (r.status === 401) { openLoginModal(BOARD, BOARD_DISPLAY); return; }
	checkForUpdates();
}

async function deleteAllMessages() {
	const r = await fetch(api('/delete_all'), { method: 'POST' });
	if (r.status === 401) { openLoginModal(BOARD, BOARD_DISPLAY); return; }
	checkForUpdates();
}

document.getElementById('image').addEventListener('change', function() {
	const fileNames = Array.from(this.files).map(file => file.name).join('; ');
	document.getElementById('image-name').textContent = fileNames;
});

document.getElementById('video').addEventListener('change', function() {
	const fileNames = Array.from(this.files).map(file => file.name).join('; ');
	document.getElementById('video-name').textContent = fileNames;
});

document.getElementById('file').addEventListener('change', function() {
	const fileNames = Array.from(this.files).map(file => file.name).join('; ');
	document.getElementById('file-name').textContent = fileNames;
});

// ===========================================================================
// Modal infrastructure
// ===========================================================================
function openModal(node) {
	const modal = document.getElementById('modal');
	const body = document.getElementById('modalBody');
	body.innerHTML = '';
	body.appendChild(node);
	modal.hidden = false;
	const focusable = body.querySelector('input, button, textarea, select');
	if (focusable) focusable.focus();
}

function closeModal() {
	document.getElementById('modal').hidden = true;
	document.getElementById('modalBody').innerHTML = '';
}

document.getElementById('modal').addEventListener('click', function(e) {
	if (e.target.hasAttribute('data-close')) closeModal();
});
document.addEventListener('keydown', function(e) {
	if (e.key === 'Escape' && !document.getElementById('modal').hidden) closeModal();
});

function renderQR(container, text) {
	try {
		const qr = qrcode(0, 'L');   // type 0 = auto-size, EC level L for capacity
		qr.addData(text);
		qr.make();
		container.innerHTML = qr.createImgTag(5, 8);
		const img = container.querySelector('img');
		if (img) { img.removeAttribute('width'); img.removeAttribute('height'); img.alt = 'TOTP QR code'; }
	} catch (e) {
		container.textContent = 'Could not render QR code — use the secret below.';
	}
}

// ===========================================================================
// Board: switch / setup / login / settings / logout
// ===========================================================================
document.getElementById('boardForm').addEventListener('submit', async function(e) {
	e.preventDefault();
	const raw = document.getElementById('boardInput').value;
	const slug = canonicalizeSlug(raw);
	if (!slug) { showToast('Enter a valid board name'); return; }
	if (slug === BOARD) { document.getElementById('boardInput').value = ''; return; }
	let r;
	try {
		r = await fetch('/b/' + slug + '/access');
	} catch (err) { showToast('Network error'); return; }
	if (r.status === 429) { showToast('Too many attempts — slow down'); return; }
	const j = await r.json().catch(() => ({}));
	if (!j.ok) { showToast(j.message || 'Invalid board name'); return; }
	if (j.action === 'open') {
		window.location = '/b/' + slug;
	} else if (j.action === 'setup') {
		openSetupModal(j.slug, j.display, j.secret, j.otpauth);
	} else if (j.action === 'login') {
		openLoginModal(j.slug, j.display);
	}
});

function openSetupModal(slug, display, secret, otpauth) {
	const node = document.createElement('div');
	node.innerHTML = `
		<h3 class="modal-title">Create board “${esc(display)}”</h3>
		<p class="modal-sub">This name is free. Protect it with an authenticator app — no password.</p>
		<div class="qr" id="qrBox"></div>
		<p class="modal-sub">Scan with your authenticator, or
			<a href="${esc(otpauth)}" class="otp-link">add it on this device</a>.</p>
		<div class="secret-row">
			<code id="secretText">${esc(secret)}</code>
			<button type="button" id="copySecret" class="btn">Copy</button>
		</div>
		<p class="warn">⚠ Save this secret. It is the <strong>only</strong> backup — lose it and the board is gone forever.</p>
		<form id="codeForm" class="code-form">
			<input id="codeInput" inputmode="numeric" autocomplete="one-time-code"
			       placeholder="6-digit code" aria-label="Authenticator code">
			<button type="submit" class="btn btn-primary">Create &amp; enter</button>
		</form>
		<div class="modal-err" id="modalErr"></div>`;
	openModal(node);
	renderQR(document.getElementById('qrBox'), otpauth);
	document.getElementById('copySecret').onclick = function() {
		navigator.clipboard.writeText(secret).then(() => showToast('Secret copied'));
	};
	document.getElementById('codeForm').onsubmit = async function(e) {
		e.preventDefault();
		const code = document.getElementById('codeInput').value.trim();
		const fd = new FormData();
		fd.append('secret', secret);
		fd.append('code', code);
		fd.append('display', display);
		const r = await fetch('/b/' + slug + '/setup', { method: 'POST', body: fd });
		if (r.ok) { window.location = '/b/' + slug; return; }
		const j = await r.json().catch(() => ({}));
		document.getElementById('modalErr').textContent = j.message || 'Setup failed.';
	};
}

function openLoginModal(slug, display) {
	const node = document.createElement('div');
	node.innerHTML = `
		<h3 class="modal-title">Board “${esc(display || slug)}”</h3>
		<p class="modal-sub">Enter the current code from your authenticator app.</p>
		<form id="codeForm" class="code-form">
			<input id="codeInput" inputmode="numeric" autocomplete="one-time-code"
			       placeholder="6-digit code" aria-label="Authenticator code">
			<button type="submit" class="btn btn-primary">Log in</button>
		</form>
		<div class="modal-err" id="modalErr"></div>`;
	openModal(node);
	document.getElementById('codeForm').onsubmit = async function(e) {
		e.preventDefault();
		const code = document.getElementById('codeInput').value.trim();
		const fd = new FormData();
		fd.append('code', code);
		const r = await fetch('/b/' + slug + '/login', { method: 'POST', body: fd });
		if (r.ok) { window.location = '/b/' + slug; return; }
		const j = await r.json().catch(() => ({}));
		document.getElementById('modalErr').textContent = j.message || 'Login failed.';
	};
}

function openSettingsModal() {
	const levels = [
		['private', 'Private', 'No public access. Code required to read or write.'],
		['read',    'Read-only', 'Anyone can read. Code required to post or delete.'],
		['append',  'Append', 'Anyone can read and post. Code required to delete.'],
		['public',  'Public', 'Anyone can read, post, and delete.'],
	];
	const radios = levels.map(([val, label, desc]) => `
		<label class="perm-option">
			<input type="radio" name="perm" value="${val}" ${val === BOARD_PERM ? 'checked' : ''}>
			<span><strong>${label}</strong> — ${desc}</span>
		</label>`).join('');
	const node = document.createElement('div');
	node.innerHTML = `
		<h3 class="modal-title">Settings — “${esc(BOARD_DISPLAY)}”</h3>
		<form id="settingsForm">
			<fieldset class="perm-set"><legend>Who can access this board</legend>${radios}</fieldset>
			<label class="field">
				<span>Auto-delete messages after</span>
				<input id="retentionInput" placeholder="inherit site default (e.g. 4h, 7d, 0=never)"
				       value="${esc(BOARD_RETENTION)}">
			</label>
			<div class="modal-err" id="modalErr"></div>
			<div class="modal-actions">
				<button type="submit" class="btn btn-primary">Save</button>
				<button type="button" id="logoutBtn" class="btn">Log out</button>
				<button type="button" id="deleteBoardBtn" class="btn btn-danger">Delete board</button>
			</div>
		</form>`;
	openModal(node);
	document.getElementById('settingsForm').onsubmit = async function(e) {
		e.preventDefault();
		const perm = node.querySelector('input[name="perm"]:checked').value;
		const retention = document.getElementById('retentionInput').value.trim();
		const fd = new FormData();
		fd.append('perm', perm);
		fd.append('retention', retention);
		const r = await fetch(api('/settings'), { method: 'POST', body: fd });
		if (r.ok) { closeModal(); checkForUpdates(); showToast('Settings saved'); return; }
		const j = await r.json().catch(() => ({}));
		document.getElementById('modalErr').textContent = j.message || 'Could not save.';
	};
	document.getElementById('logoutBtn').onclick = doLogout;
	document.getElementById('deleteBoardBtn').onclick = async function() {
		if (!confirm('Delete this board and everything in it? This cannot be undone.')) return;
		const r = await fetch(api('/delete_board'), { method: 'POST' });
		if (r.ok) { window.location = '/'; }
	};
}

async function doLogout() {
	await fetch(api('/logout'), { method: 'POST' });
	window.location = '/b/' + BOARD;
}

// ===========================================================================
// Board context strip + permission-driven visibility
// ===========================================================================
function renderLocked() {
	applyPermUI();
	const panel = document.getElementById('lockedPanel');
	const messages = document.getElementById('messages');
	if (messages) messages.innerHTML = '';
	panel.hidden = false;
	panel.innerHTML = `
		<div class="locked-inner">
			<div class="locked-icon">🔒</div>
			<p>This board is private.</p>
			<button type="button" class="btn btn-primary" id="lockedLogin">Enter code</button>
		</div>`;
	document.getElementById('lockedLogin').onclick = function() { openLoginModal(BOARD, BOARD_DISPLAY); };
	renderBoardContext();
}

function applyPermUI() {
	const compose = document.getElementById('messageForm');
	if (compose) compose.style.display = (canPost() && !BOARD_LOCKED) ? '' : 'none';
	const delAll = document.getElementById('deleteAllBtn');
	if (delAll) delAll.style.display = (canDelete() && !BOARD_LOCKED) ? '' : 'none';
	const panel = document.getElementById('lockedPanel');
	if (panel && !BOARD_LOCKED) panel.hidden = true;
	renderBoardContext();
}

function renderBoardContext() {
	const ctx = document.getElementById('boardContext');
	if (!ctx) return;
	if (!BOARD) {            // default public board: keep the bar out of the way
		ctx.hidden = true;
		ctx.innerHTML = '';
		return;
	}
	ctx.hidden = false;
	const permLabel = { private: 'private', read: 'read-only', append: 'append', public: 'public' }[BOARD_PERM] || BOARD_PERM;
	let actions = `<a class="board-home" href="/">← Public board</a>`;
	if (BOARD_AUTHED) {
		actions += `<button type="button" class="board-action" id="ctxSettings">Settings</button>`;
		actions += `<button type="button" class="board-action" id="ctxLogout">Log out</button>`;
	} else {
		actions += `<button type="button" class="board-action" id="ctxLogin">Log in</button>`;
	}
	ctx.innerHTML = `
		<span class="board-name">${esc(BOARD_DISPLAY)}</span>
		<span class="perm-badge perm-${BOARD_PERM}">${permLabel}</span>
		<span class="board-actions">${actions}</span>`;
	const s = document.getElementById('ctxSettings');
	if (s) s.onclick = openSettingsModal;
	const lo = document.getElementById('ctxLogout');
	if (lo) lo.onclick = doLogout;
	const li = document.getElementById('ctxLogin');
	if (li) li.onclick = function() { openLoginModal(BOARD, BOARD_DISPLAY); };
}

window.onload = function() {
	applyPermUI();
	checkForUpdates();
};
