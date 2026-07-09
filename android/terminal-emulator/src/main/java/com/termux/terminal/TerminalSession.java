package com.termux.terminal;

import android.os.Handler;
import android.os.Looper;

import java.util.UUID;

/**
 * A terminal session backed by a <em>remote</em> PTY (CLA-14).
 *
 * Upstream Termux uses {@link com.termux.terminal.TerminalEmulator} together with a local
 * subprocess spawned via JNI. The Mobaide Android client does not run a local shell —
 * the PTY lives on the server and bytes flow over a binary WebSocket. This stripped variant
 * keeps the same public surface that {@link com.termux.view.TerminalView} expects, but:
 *
 * <ul>
 *   <li>No subprocess is spawned, no native library is loaded.</li>
 *   <li>{@link #emitOutput(byte[], int)} is the entry point for PTY bytes received from the
 *       network — they are appended to the emulator on the main thread.</li>
 *   <li>{@link #write(byte[], int, int)} (called by {@link TerminalEmulator} / TerminalView when
 *       the user types) is forwarded to a {@link TerminalInputListener} provided by the host.</li>
 * </ul>
 */
public final class TerminalSession extends TerminalOutput {

    public final String mHandle = UUID.randomUUID().toString();

    TerminalEmulator mEmulator;

    TerminalSessionClient mClient;

    /** Set by the application for user identification of session, not by terminal. */
    public String mSessionName;

    private final Integer mTranscriptRows;
    private final Handler mMainThreadHandler = new Handler(Looper.getMainLooper());

    private TerminalInputListener mInputListener;
    private boolean mFinished;

    /**
     * Safety valve for synchronized output (DECSET 2026): if a program holds the mode set
     * longer than this, the current screen state is rendered anyway (the mode bit stays
     * set, per spec — only the paint is forced). Chosen above typical BSU..ESU frame times
     * (a few ms..tens of ms) and below human-noticeable stall (~200 ms).
     */
    private static final long SYNC_UPDATE_TIMEOUT_MS = 150;

    /** Whether a deferred sync-mode flush is scheduled; main-thread only. */
    private boolean mSyncFlushPending;

    private final Runnable mSyncFlushRunnable = new Runnable() {
        @Override
        public void run() {
            mSyncFlushPending = false;
            notifyScreenUpdate();
        }
    };

    public interface TerminalInputListener {
        void onInputBytes(byte[] data, int offset, int count);
    }

    public TerminalSession(TerminalSessionClient client) {
        this(null, client);
    }

    public TerminalSession(Integer transcriptRows, TerminalSessionClient client) {
        this.mTranscriptRows = transcriptRows;
        this.mClient = client;
    }

    /** Provided for source compatibility with upstream Termux callers. Shell args are ignored. */
    public TerminalSession(String shellPath, String cwd, String[] args, String[] env,
                           Integer transcriptRows, TerminalSessionClient client) {
        this(transcriptRows, client);
    }

    public void setOnInputListener(TerminalInputListener listener) {
        mInputListener = listener;
    }

    public void updateTerminalSessionClient(TerminalSessionClient client) {
        mClient = client;
        if (mEmulator != null) mEmulator.updateTerminalSessionClient(client);
    }

    /** Inform the emulator of the new (columns, rows) — no PTY ioctl, the server owns the PTY. */
    public void updateSize(int columns, int rows, int cellWidthPixels, int cellHeightPixels) {
        if (mEmulator == null) {
            initializeEmulator(columns, rows, cellWidthPixels, cellHeightPixels);
        } else {
            mEmulator.resize(columns, rows, cellWidthPixels, cellHeightPixels);
        }
    }

    public String getTitle() {
        return (mEmulator == null) ? null : mEmulator.getTitle();
    }

    public void initializeEmulator(int columns, int rows, int cellWidthPixels, int cellHeightPixels) {
        mEmulator = new TerminalEmulator(this, columns, rows, cellWidthPixels, cellHeightPixels,
                mTranscriptRows, mClient);
    }

    /**
     * Feed PTY output bytes received from the server into the emulator. Safe to call from any
     * thread — the actual append + screen-update notification is posted to the main thread.
     */
    public void emitOutput(final byte[] data, final int length) {
        mMainThreadHandler.post(new Runnable() {
            @Override
            public void run() {
                if (mEmulator == null) return;
                mEmulator.append(data, length);
                if (mEmulator.isSynchronizedOutputActive()) {
                    // Synchronized output (DECSET 2026): parsing continues, but the
                    // screen-update fan-out (invalidate + mode sampling) is deferred so a
                    // multi-write TUI frame paints atomically at the ESU chunk. The timer
                    // is leading-edge: a program streaming inside a long sync window still
                    // paints every SYNC_UPDATE_TIMEOUT_MS instead of being starved.
                    if (!mSyncFlushPending) {
                        mSyncFlushPending = true;
                        mMainThreadHandler.postDelayed(mSyncFlushRunnable, SYNC_UPDATE_TIMEOUT_MS);
                    }
                } else {
                    if (mSyncFlushPending) {
                        mSyncFlushPending = false;
                        mMainThreadHandler.removeCallbacks(mSyncFlushRunnable);
                    }
                    notifyScreenUpdate();
                }
            }
        });
    }

    /** Called by the emulator / view when input bytes need to be sent to the (remote) PTY. */
    @Override
    public void write(byte[] data, int offset, int count) {
        if (mFinished || mInputListener == null || count <= 0) return;
        mInputListener.onInputBytes(data, offset, count);
    }

    public void writeCodePoint(boolean prependEscape, int codePoint) {
        if (codePoint > 1114111 || (codePoint >= 0xD800 && codePoint <= 0xDFFF)) {
            throw new IllegalArgumentException("Invalid code point: " + codePoint);
        }
        byte[] buf = new byte[5];
        int p = 0;
        if (prependEscape) buf[p++] = 27;
        if (codePoint <= 0b1111111) {
            buf[p++] = (byte) codePoint;
        } else if (codePoint <= 0b11111111111) {
            buf[p++] = (byte) (0b11000000 | (codePoint >> 6));
            buf[p++] = (byte) (0b10000000 | (codePoint & 0b111111));
        } else if (codePoint <= 0b1111111111111111) {
            buf[p++] = (byte) (0b11100000 | (codePoint >> 12));
            buf[p++] = (byte) (0b10000000 | ((codePoint >> 6) & 0b111111));
            buf[p++] = (byte) (0b10000000 | (codePoint & 0b111111));
        } else {
            buf[p++] = (byte) (0b11110000 | (codePoint >> 18));
            buf[p++] = (byte) (0b10000000 | ((codePoint >> 12) & 0b111111));
            buf[p++] = (byte) (0b10000000 | ((codePoint >> 6) & 0b111111));
            buf[p++] = (byte) (0b10000000 | (codePoint & 0b111111));
        }
        write(buf, 0, p);
    }

    public TerminalEmulator getEmulator() {
        return mEmulator;
    }

    protected void notifyScreenUpdate() {
        if (mClient != null) mClient.onTextChanged(this);
    }

    public void reset() {
        if (mEmulator != null) mEmulator.reset();
        notifyScreenUpdate();
    }

    /** Mark the session as finished. No SIGKILL — the server controls the remote process. */
    public void finishIfRunning() {
        if (!mFinished) {
            mFinished = true;
            if (mClient != null) mClient.onSessionFinished(this);
        }
    }

    @Override
    public void titleChanged(String oldTitle, String newTitle) {
        if (mClient != null) mClient.onTitleChanged(this);
    }

    public synchronized boolean isRunning() {
        return !mFinished;
    }

    public synchronized int getExitStatus() {
        return 0;
    }

    @Override
    public void onCopyTextToClipboard(String text) {
        if (mClient != null) mClient.onCopyTextToClipboard(this, text);
    }

    @Override
    public void onPasteTextFromClipboard() {
        if (mClient != null) mClient.onPasteTextFromClipboard(this);
    }

    @Override
    public void onBell() {
        if (mClient != null) mClient.onBell(this);
    }

    @Override
    public void onColorsChanged() {
        if (mClient != null) mClient.onColorsChanged(this);
    }

    public int getPid() {
        return -1;
    }

    /** Returns null — the working directory lives on the remote server. */
    public String getCwd() {
        return null;
    }
}
