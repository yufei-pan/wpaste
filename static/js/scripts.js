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
const POLL_INTERVAL = 5000;
const POLL_TIMEOUT = 4000;      // < POLL_INTERVAL so a hung server reads as offline
let lastKnownUpdate = 0;
let lastContact = 0;            // Date.now() of the last response from the server
let pollTimer = null;
let pollBusy = false;
let pollAgain = false;

function stopPolling() {
	if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}

// fetch() with a deadline and no caching, so the indicator reflects a real
// round trip to the server.
async function fetchFresh(url, ms) {
	const ctrl = new AbortController();
	const timer = setTimeout(() => ctrl.abort(), ms);
	try {
		return await fetch(url, { signal: ctrl.signal, cache: 'no-store' });
	} finally {
		clearTimeout(timer);
	}
}

// Connection indicator. live = the last request reached the server;
// offline = no response / timeout / 5xx (what a proxy returns when wpaste is
// down); error = the server answered with another failure; missing = the
// server is up but this board hasn't been set up; locked = polling stopped
// because this board needs a code; connecting = no answer yet.
const CONN_LABELS = { connecting: 'connecting', live: 'live', offline: 'offline', error: 'error', missing: 'no board', locked: 'locked' };

function setConnState(state, detail) {
	const el = document.getElementById('liveDot');
	if (!el) return;
	if (state === 'live') lastContact = Date.now();
	const since = lastContact ? ' Last contact ' + new Date(lastContact).toLocaleTimeString() + '.' : '';
	el.title = {
		connecting: 'Connecting to the server…',
		live: 'Connected — checking for new messages every ' + (POLL_INTERVAL / 1000) + ' seconds.',
		offline: "Can't reach the server (" + detail + ').' + since + ' Retrying every ' + (POLL_INTERVAL / 1000) + ' seconds.',
		error: 'The server answered with an error (' + detail + ').' + since + ' Retrying every ' + (POLL_INTERVAL / 1000) + ' seconds.',
		missing: "Connected, but this board doesn't exist yet.",
		locked: 'Not updating — this board needs a code to read.',
	}[state];
	if (el.dataset.state === state) return;
	el.dataset.state = state;
	const label = el.querySelector('.live-label');
	if (label) label.textContent = CONN_LABELS[state];
}

function markHttpError(status) {
	if (status === 404) { lastContact = Date.now(); setConnState('missing'); return; }
	setConnState(status >= 500 ? 'offline' : 'error', 'HTTP ' + status);
}

function markFetchError(e) {
	setConnState('offline', e && e.name === 'AbortError' ? 'timed out' : 'network error');
}

async function checkForUpdates() {
	// Callers (post, delete, settings, timers) can overlap; coalesce them into
	// one in-flight poll plus at most one follow-up.
	if (pollBusy) { pollAgain = true; return; }
	pollBusy = true;
	try {
		do {
			pollAgain = false;
			await pollOnce();
		} while (pollAgain);
	} finally {
		pollBusy = false;
	}
}

async function pollOnce() {
	let response;
	try {
		response = await fetchFresh(api('/last-update'), POLL_TIMEOUT);
	} catch (e) {
		markFetchError(e);
		return;
	}
	if (response.status === 401) {   // private board, not authed
		BOARD_LOCKED = true;
		stopPolling();               // don't keep hammering a board we can't read
		setConnState('locked');
		renderLocked();
		return;
	}
	if (!response.ok) { markHttpError(response.status); return; }
	let data;
	try {
		data = await response.json();
	} catch (e) {
		setConnState('error', 'bad response');
		return;
	}
	setConnState('live');
	// Only advance once the messages actually loaded, so a failed fetch is
	// retried on the next poll instead of leaving the list stale.
	if (data.last_update > lastKnownUpdate && await fetchMessages()) {
		lastKnownUpdate = data.last_update;
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

pollTimer = setInterval(checkForUpdates, POLL_INTERVAL);

// Background tabs get their timers throttled (to once a minute in Chrome), so
// re-check as soon as the tab is visible again or the network comes back.
document.addEventListener('visibilitychange', function() {
	if (!document.hidden && pollTimer) checkForUpdates();
});
window.addEventListener('online', function() { if (pollTimer) checkForUpdates(); });
window.addEventListener('offline', function() { if (pollTimer) setConnState('offline', 'browser is offline'); });

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
			uploadProgress.textContent = xhr.status === 413 ? 'Too large' : 'Upload failed';
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
//
// Text renders only on clear signs: code and configs share '<', '#' and '-'
// with HTML and Markdown, and must stay plain. Copy always returns the
// original text, whatever is shown.
// ===========================================================================
function isHTML(str) {
	const s = str.trim();
	if (!s.startsWith('<')) {
		return false;
	}
	const doc = new DOMParser().parseFromString(s, 'text/html');
	const elements = [...doc.head.querySelectorAll('*'), ...doc.body.querySelectorAll('*')];
	// `<int>` in `vector<int>` parses as a made-up tag; real HTML uses known ones.
	return elements.length > 0 && !elements.some((el) => el instanceof HTMLUnknownElement);
}

// Signs that rarely appear outside Markdown.
const MARKDOWN_STRONG = [
	/```[\s\S]*?```/,                                       // fenced code block
	/\[[^\]\n]+\]\((https?:\/\/|\/|#|\.\.?\/)[^)\s]*\)/,    // [link](url)
	/^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/m,   // table separator row
	/(^|[\s(])\*\*\S(?:[^*\n]*\S)?\*\*/m,                   // **bold**
];
// Signs that code and configs share ('# comment', '- item'): two are needed,
// and none of the code hints below.
const MARKDOWN_WEAK = [
	/^#{1,6}\s+\S/m,
	/^\s*[-*+]\s+\S/m,
	/^\s*\d+\.\s+\S/m,
	/^\s*>\s+\S/m,
	/`[^`\n]+`/,
	/^\S.*\n(={3,}|-{3,})\s*$/m,
];

function looksLikeCode(str) {
	return /^#!/.test(str)                                       // shebang
		|| /[;{}]\s*$/m.test(str)                                // statement / block ends
		|| (str.match(/^\s*[\w.-]+:(\s|$)/gm) || []).length >= 2; // YAML-style keys
}

function looksLikeMarkdown(str) {
	if (!str || !str.trim()) {
		return false;
	}
	if (MARKDOWN_STRONG.some((re) => re.test(str))) {
		return true;
	}
	return MARKDOWN_WEAK.filter((re) => re.test(str)).length >= 2 && !looksLikeCode(str);
}

// <style> would restyle the whole page (a pasted HTML document could blank it).
function sanitize(html) {
	return DOMPurify.sanitize(html, { FORBID_TAGS: ['style'] });
}

function renderHTML(content, mode) {
	return sanitize(mode === 'markdown' ? marked.parse(content, { breaks: true }) : content);
}

function buildTextElement(content, mode) {
	if (mode === 'plain') {
		const pre = document.createElement('pre');
		pre.textContent = content;
		return pre;
	}
	const div = document.createElement('div');
	if (mode === 'markdown') {
		div.classList.add('markdown-body');
	}
	div.innerHTML = renderHTML(content, mode);
	return div;
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

// Returns true once the list has been re-rendered from the server.
async function fetchMessages() {
    let response;
    try {
        response = await fetchFresh(api('/messages'), 30000);
    } catch (e) {
        markFetchError(e);
        return false;
    }
    if (response.status === 401) {
        BOARD_LOCKED = true;
        stopPolling();
        setConnState('locked');
        renderLocked();
        return false;
    }
    if (!response.ok) { markHttpError(response.status); return false; }
    let result;
    try {
        result = await response.json();
    } catch (e) {
        setConnState('error', 'bad response');
        return false;
    }

    // Refresh board state from the response and re-skin the UI accordingly.
    BOARD_LOCKED = false;
    if (BOARD) {
        BOARD_PERM = result.perm || BOARD_PERM;
        BOARD_AUTHED = !!result.authed;
        BOARD_DISPLAY = result.display || BOARD;
        BOARD_RETENTION = result.retention || '';
    }
    applyPermUI();
    renderMessages(result.messages);
    return true;
}

// Rendered messages by id: an update only adds and removes what changed, so a
// playing video or a Show Raw toggle survives someone else posting.
const renderedMessages = new Map();
let renderedWithDelete = null;

function clearMessages() {
	renderedMessages.clear();
	document.getElementById('messages').innerHTML = '';
}

function renderMessages(list) {
	if (renderedWithDelete !== canDelete()) {   // Delete buttons follow permissions
		clearMessages();
		renderedWithDelete = canDelete();
	}
	const messagesDiv = document.getElementById('messages');
	const live = new Set(list.map((m) => m.id));
	for (const [id, entry] of renderedMessages) {
		if (!live.has(id)) {
			entry.el.remove();
			renderedMessages.delete(id);
		}
	}
	let next = messagesDiv.firstChild;
	for (const message of list) {
		let entry = renderedMessages.get(message.id);
		if (!entry) {
			entry = buildMessage(message);
			renderedMessages.set(message.id, entry);
		}
		if (entry.el === next) {
			next = next.nextSibling;
		} else {
			messagesDiv.insertBefore(entry.el, next);
		}
	}
}

// One message card. `view` is what the content area shows right now (Show Raw
// swaps it); Copy works from the message itself, never from the rendering.
function buildMessage(message) {
	const messageElement = document.createElement('div');
	messageElement.classList.add('message');
	messageElement.id = `message-${message.id}`;
	messageElement.dataset.id = message.id;

	const contentContainer = document.createElement('div');
	contentContainer.classList.add('content-container');

	const textMode = message.type === 'text' ? detectTextMode(message.content) : 'plain';
	const view = { el: null, mode: textMode };

	if (message.type === 'text') {
		view.el = buildTextElement(message.content, textMode);

	} else if (message.type === 'image') {
		if (message.filename && message.filename !== 'image.png') {
			const imgName = document.createElement('p');
			imgName.textContent = message.filename;
			contentContainer.appendChild(imgName);
		}
		const img = document.createElement('img');
		img.src = message.content;
		img.style.maxWidth = '100%';
		view.el = img;

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
		view.el = video;

	} else if (message.type === 'file') {
		const a = document.createElement('a');
		a.href = message.content;
		a.textContent = message.filename || 'Download File';
		a.download = '';
		view.el = a;

	} else {
		console.error('Unknown message type:', message.type);
		const pre = document.createElement('pre');
		pre.textContent = 'Unknown message type';
		view.el = pre;
	}
	contentContainer.appendChild(view.el);
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
	copyButton.onclick = function() { copyMessage(message, view); };
	buttonsContainer.appendChild(copyButton);

	const copyLinkButton = document.createElement('button');
	copyLinkButton.textContent = 'Copy Links';
	copyLinkButton.classList.add('copy-link-button');
	copyLinkButton.onclick = function() {
		const url = messageRawUrl(message);
		if (!url) { showToast('No link available'); return; }
		copyLinkToClipboard(url, contentContainer, view);
	};
	buttonsContainer.appendChild(copyLinkButton);

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
			const isCurrentlyRaw = view.mode === 'plain';
			const newMode = isCurrentlyRaw ? textMode : 'plain';
			const newElement = buildTextElement(message.content, newMode);
			contentContainer.replaceChild(newElement, view.el);
			view.el = newElement;
			view.mode = newMode;
			messageElement.setAttribute('data-show-raw', isCurrentlyRaw ? 'false' : 'true');
			showRawButton.textContent = isCurrentlyRaw ? 'Show Raw' : 'Show Rendered';
		};

		buttonsContainer.appendChild(showRawButton);
	}

	messageElement.appendChild(buttonsContainer);
	return { el: messageElement, message: message, view: view };
}

// ===========================================================================
// Clipboard
//
// Text goes through execCommand('copy') with our own copy-event data: unlike
// navigator.clipboard it also works over plain HTTP (a LAN address), and it
// sets plain text and HTML in one go. Only images need navigator.clipboard.
// ===========================================================================
let pendingCopy = null;      // formats for the copy event copyFormats() fires

function copyFormats(formats) {
	const previous = document.activeElement;
	// Safari only fires the copy event when something is selected.
	const ta = document.createElement('textarea');
	ta.value = formats['text/plain'] || '';
	ta.setAttribute('readonly', '');
	ta.style.position = 'fixed';
	ta.style.opacity = '0';
	document.body.appendChild(ta);
	ta.select();
	pendingCopy = formats;
	let ok = false;
	try {
		ok = document.execCommand('copy');
	} catch (e) {
		ok = false;
	}
	pendingCopy = null;
	ta.remove();
	if (previous && previous.focus) previous.focus();
	return ok;
}

// What Copy puts on the clipboard: a text message's original text (plus its
// rendering as HTML while rendered), or a link to anything else.
function messageFormats(message, view) {
	if (message.type === 'text') {
		const formats = { 'text/plain': message.content };
		if (view.mode !== 'plain') formats['text/html'] = renderHTML(message.content, view.mode);
		return formats;
	}
	return { 'text/plain': messageRawUrl(message) || '' };
}

function copiedLabel(message) {
	return message.type === 'text' ? 'Text copied to clipboard!' : 'Link copied to clipboard!';
}

function canWriteImages() {
	return typeof ClipboardItem !== 'undefined' && !!navigator.clipboard;
}

function copyMessage(message, view) {
	if (message.type === 'image' && canWriteImages()) {
		copyImage(view.el);
		return;
	}
	showToast(copyFormats(messageFormats(message, view)) ? copiedLabel(message) : 'Copy failed');
}

function absoluteUrl(pathOrUrl) {
	if (/^https?:\/\//i.test(pathOrUrl)) return pathOrUrl;
	return window.location.origin + (pathOrUrl.startsWith('/') ? pathOrUrl : '/' + pathOrUrl);
}

function messageRawUrl(message) {
	if (message.type === 'text') {
		return absoluteUrl(api('/text/' + message.id));
	}
	if (message.type === 'image' || message.type === 'video' || message.type === 'file') {
		return absoluteUrl(message.content);
	}
	return null;
}

function showRawLinkFallback(url, contentContainer, view) {
	showToast('Clipboard unavailable — copy the link below');
	const pre = document.createElement('pre');
	pre.classList.add('raw-link');
	pre.textContent = url;
	if (view.el && view.el.parentNode === contentContainer) {
		contentContainer.replaceChild(pre, view.el);
	} else {
		contentContainer.prepend(pre);
	}
	view.el = pre;
}

function copyLinkToClipboard(url, contentContainer, view) {
	if (copyFormats({ 'text/plain': url })) {
		showToast('Link copied to clipboard!');
	} else {
		showRawLinkFallback(url, contentContainer, view);
	}
}

function copyImage(img) {
	fetch(img.src)
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
				const image = new Image();
				image.onload = () => {
					canvas.width = image.width;
					canvas.height = image.height;
					ctx.drawImage(image, 0, 0);
					canvas.toBlob((pngBlob) => {
						const item = new ClipboardItem({ "image/png": pngBlob });
						navigator.clipboard.write([item]).then(() => {
							showToast('Converted Image copied to clipboard!');
						}, (err) => {
							showToast('Failed to copy image: ' + err);
						});
					}, 'image/png');
				};
				image.src = URL.createObjectURL(blob);
			}
		})
		.catch((err) => showToast('Failed to copy image: ' + err));
}

// True when focus is in any editable field (the compose box, the board name
// box, a TOTP/code input, etc.) — those should keep native copy/paste.
function inEditableField() {
	return isEditable(document.activeElement);
}

function isEditable(el) {
	return !!el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable);
}

document.addEventListener('copy', function(e) {
	if (pendingCopy) {                 // fired by copyFormats()
		for (const [type, data] of Object.entries(pendingCopy)) {
			e.clipboardData.setData(type, data);
		}
		e.preventDefault();
		return;
	}
	if (inEditableField() || window.getSelection().toString().trim()) {
		return;
	}
	// Nothing selected: copy the newest message, as its Copy button would.
	const newest = document.querySelector('#messages > .message');
	const entry = newest && renderedMessages.get(newest.dataset.id);
	if (!entry) {
		return;
	}
	e.preventDefault();
	if (entry.message.type === 'image' && canWriteImages()) {
		copyImage(entry.view.el);
		return;
	}
	for (const [type, data] of Object.entries(messageFormats(entry.message, entry.view))) {
		e.clipboardData.setData(type, data);
	}
	showToast(copiedLabel(entry.message));
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
				return;
			}
			if (!response.ok) {
				showToast(response.status === 413 ? 'Too large to upload' : 'Upload failed');
				return;
			}
			checkForUpdates();
		})
		.catch(error => { console.error('Error:', error); showToast('Upload failed'); });
}

function uploadField(file) {
	if (file.type.startsWith('image/')) return 'image';
	if (file.type.startsWith('video/')) return 'video';
	return 'file';
}

// One paste or drop makes one message: the files if there are any, otherwise
// the plain text (a rich copy also carries an HTML twin of the same text).
function postTransfer(transfer) {
	const formData = new FormData();
	const files = Array.from(transfer.files || []);
	if (files.length) {
		for (const file of files) {
			formData.append(uploadField(file), file);
		}
	} else {
		const text = transfer.getData('text/plain') || transfer.getData('text/html');
		if (!text.trim()) return;
		formData.append('message', text);
	}
	postFormData(formData);
}

document.body.addEventListener('paste', function(e) {
	if (inEditableField()) {
		return;   // let the focused field (compose, board name, TOTP code…) paste normally
	}
	if (!canPost()) return;
	e.preventDefault();
	postTransfer(e.clipboardData);
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
	e.target.style.background = '';
	if (isEditable(e.target) && !e.dataTransfer.files.length) {
		return;   // text dropped into a field (the compose box…) lands in the field
	}
	e.preventDefault();
	e.stopPropagation();
	if (!canPost()) { showToast('This board is read-only.'); return; }
	postTransfer(e.dataTransfer);
});

async function deleteMessage(messageIndex) {
	const r = await fetch(api(`/delete/${messageIndex}`), { method: 'POST' });
	if (r.status === 401) { openLoginModal(BOARD, BOARD_DISPLAY); return; }
	checkForUpdates();
}

async function deleteAllMessages() {
	if (!confirm('Delete all messages on this board?')) return;
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
		showToast(copyFormats({ 'text/plain': secret }) ? 'Secret copied' : 'Copy failed — select the secret and copy it');
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
	clearMessages();
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
