package com.termux.view.textselection;

import android.annotation.SuppressLint;
import android.graphics.Canvas;
import android.graphics.Rect;
import android.graphics.drawable.Drawable;
import android.os.Build;
import android.os.SystemClock;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.ViewParent;
import android.view.WindowManager;
import android.widget.Magnifier;
import android.widget.PopupWindow;

import com.termux.view.R;
import com.termux.view.TerminalView;
import com.termux.view.support.PopupWindowCompatGingerbread;

@SuppressLint("ViewConstructor")
public class TextSelectionHandleView extends View {
    private final TerminalView terminalView;
    private PopupWindow mHandle;
    private final CursorController mCursorController;

    private final Drawable mHandleLeftDrawable;
    private final Drawable mHandleRightDrawable;
    private Drawable mHandleDrawable;

    private boolean mIsDragging;

    final int[] mTempCoords = new int[2];
    Rect mTempRect;

    private int mPointX;
    private int mPointY;
    private float mTouchToWindowOffsetX;
    private float mTouchToWindowOffsetY;
    private float mHotspotX;
    private float mHotspotY;
    private float mTouchOffsetY;
    private int mLastParentX;
    private int mLastParentY;

    private int mHandleHeight;
    private int mHandleWidth;

    private final int mInitialOrientation;
    private int mOrientation;

    public static final int LEFT = 0;
    public static final int RIGHT = 2;

    private long mLastTime;

    private Magnifier mMagnifier;

    private float mLastRawX;
    private float mLastRawY;
    private static final long AUTOSCROLL_INTERVAL_MS = 45;
    /**
     * Repeats the drag from the last touch while the handle sits at an edge: {@code ACTION_MOVE}
     * stops arriving once the finger is still, which would freeze the autoscroll short of the text.
     */
    private final Runnable mAutoscrollTick = this::dragToLastTouch;
    /**
     * Whether {@link #mAutoscrollTick} is queued. While it is, moves only record the touch so the
     * scroll keeps the tick's pace instead of stepping on every touch sample.
     */
    private boolean mAutoscrollPending;

    public TextSelectionHandleView(TerminalView terminalView, CursorController cursorController, int initialOrientation) {
        super(terminalView.getContext());
        this.terminalView = terminalView;
        mCursorController = cursorController;
        mInitialOrientation = initialOrientation;

        mHandleLeftDrawable = getContext().getDrawable(R.drawable.text_select_handle_left_material);
        mHandleRightDrawable = getContext().getDrawable(R.drawable.text_select_handle_right_material);

        setOrientation(mInitialOrientation);
    }

    private void initHandle() {
        mHandle = new PopupWindow(terminalView.getContext(), null,
            android.R.attr.textSelectHandleWindowStyle);
        mHandle.setSplitTouchEnabled(true);
        mHandle.setClippingEnabled(false);
        mHandle.setWidth(ViewGroup.LayoutParams.WRAP_CONTENT);
        mHandle.setHeight(ViewGroup.LayoutParams.WRAP_CONTENT);
        mHandle.setBackgroundDrawable(null);
        mHandle.setAnimationStyle(0);

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            mHandle.setWindowLayoutType(WindowManager.LayoutParams.TYPE_APPLICATION_SUB_PANEL);
            mHandle.setEnterTransition(null);
            mHandle.setExitTransition(null);
        } else {
            PopupWindowCompatGingerbread.setWindowLayoutType(mHandle, WindowManager.LayoutParams.TYPE_APPLICATION_SUB_PANEL);
        }
        mHandle.setContentView(this);
    }

    public void setOrientation(int orientation) {
        mOrientation = orientation;
        int handleWidth = 0;
        switch (orientation) {
            case LEFT: {
                mHandleDrawable = mHandleLeftDrawable;
                handleWidth = mHandleDrawable.getIntrinsicWidth();
                mHotspotX = (handleWidth * 3) / (float) 4;
                break;
            }

            case RIGHT: {
                mHandleDrawable = mHandleRightDrawable;
                handleWidth = mHandleDrawable.getIntrinsicWidth();
                mHotspotX = handleWidth / (float) 4;
                break;
            }
        }

        mHandleHeight = mHandleDrawable.getIntrinsicHeight();

        mHandleWidth = handleWidth;
        mTouchOffsetY = -mHandleHeight * 0.3f;
        mHotspotY = 0;
        invalidate();
    }

    public void show() {
        if (!isPositionVisible()) {
            hide();
            return;
        }

        // We remove handle from its parent first otherwise the following exception may be thrown
        // java.lang.IllegalStateException: The specified child already has a parent. You must call removeView() on the child's parent first.
        removeFromParent();

        initHandle(); // init the handle
        invalidate(); // invalidate to make sure onDraw is called

        final int[] coords = mTempCoords;
        terminalView.getLocationInWindow(coords);
        coords[0] += mPointX;
        coords[1] += mPointY;

        if (mHandle != null)
            mHandle.showAtLocation(terminalView, 0, coords[0], coords[1]);
    }

    public void hide() {
        mIsDragging = false;
        stopAutoscroll();
        dismissMagnifier();

        if (mHandle != null) {
            mHandle.dismiss();

            // We remove handle from its parent, otherwise it may still be shown in some cases even after the dismiss call
            removeFromParent();
            mHandle = null;  // garbage collect the handle
        }
        invalidate();
    }

    public void removeFromParent() {
        if (!isParentNull()) {
            ((ViewGroup)this.getParent()).removeView(this);
        }
    }

    public void positionAtCursor(final int cx, final int cy, boolean forceOrientationCheck) {
        int x = terminalView.getPointX(cx);
        int y = terminalView.getPointY(cy + 1);
        moveTo(x, y, forceOrientationCheck);
    }

    private void moveTo(int x, int y, boolean forceOrientationCheck) {
        float oldHotspotX = mHotspotX;
        checkChangedOrientation(x, forceOrientationCheck);
        mPointX = (int) (x - (isShowing() ? oldHotspotX : mHotspotX));
        mPointY = y;

        if (isPositionVisible()) {
            int[] coords = null;

            if (isShowing()) {
                coords = mTempCoords;
                terminalView.getLocationInWindow(coords);
                int x1 = coords[0] + mPointX;
                int y1 = coords[1] + mPointY;
                if (mHandle != null)
                    mHandle.update(x1, y1, getWidth(), getHeight());
            } else {
                show();
            }

            if (mIsDragging) {
                showMagnifier();
                if (coords == null) {
                    coords = mTempCoords;
                    terminalView.getLocationInWindow(coords);
                }
                if (coords[0] != mLastParentX || coords[1] != mLastParentY) {
                    mTouchToWindowOffsetX += coords[0] - mLastParentX;
                    mTouchToWindowOffsetY += coords[1] - mLastParentY;
                    mLastParentX = coords[0];
                    mLastParentY = coords[1];
                }
            }
        } else {
            hide();
        }
    }

    public void changeOrientation(int orientation) {
        if (mOrientation != orientation) {
            setOrientation(orientation);
        }
    }

    private void checkChangedOrientation(int posX, boolean force) {
        if (!mIsDragging && !force) {
            return;
        }
        long millis = SystemClock.currentThreadTimeMillis();
        if (millis - mLastTime < 50 && !force) {
            return;
        }
        mLastTime = millis;

        final TerminalView hostView = terminalView;
        final int left = hostView.getLeft();
        final int right = hostView.getWidth();
        final int top = hostView.getTop();
        final int bottom = hostView.getHeight();

        if (mTempRect == null) {
            mTempRect = new Rect();
        }
        final Rect clip = mTempRect;
        clip.left = left + terminalView.getPaddingLeft();
        clip.top = top + terminalView.getPaddingTop();
        clip.right = right - terminalView.getPaddingRight();
        clip.bottom = bottom - terminalView.getPaddingBottom();

        final ViewParent parent = hostView.getParent();
        if (parent == null || !parent.getChildVisibleRect(hostView, clip, null)) {
            return;
        }

        if (posX - mHandleWidth < clip.left) {
            changeOrientation(RIGHT);
        } else if (posX + mHandleWidth > clip.right) {
            changeOrientation(LEFT);
        } else {
            changeOrientation(mInitialOrientation);
        }
    }

    private boolean isPositionVisible() {
        // Always show a dragging handle.
        if (mIsDragging) {
            return true;
        }

        final TerminalView hostView = terminalView;
        final int left = 0;
        final int right = hostView.getWidth();
        final int top = 0;
        final int bottom = hostView.getHeight();

        if (mTempRect == null) {
            mTempRect = new Rect();
        }
        final Rect clip = mTempRect;
        clip.left = left + terminalView.getPaddingLeft();
        clip.top = top + terminalView.getPaddingTop();
        clip.right = right - terminalView.getPaddingRight();
        clip.bottom = bottom - terminalView.getPaddingBottom();

        final ViewParent parent = hostView.getParent();
        if (parent == null || !parent.getChildVisibleRect(hostView, clip, null)) {
            return false;
        }

        final int[] coords = mTempCoords;
        hostView.getLocationInWindow(coords);
        final int posX = coords[0] + mPointX + (int) mHotspotX;
        final int posY = coords[1] + mPointY + (int) mHotspotY;

        return posX >= clip.left && posX <= clip.right &&
            posY >= clip.top && posY <= clip.bottom;
    }

    /**
     * Magnifies the cell this handle points at while it is dragged, so the text under the finger
     * stays readable. Centred on the snapped handle position rather than the raw touch, and on the
     * middle of the selected row: {@link #positionAtCursor} places {@code mPointY} at its bottom edge.
     */
    private void showMagnifier() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.P) return;
        if (mMagnifier == null) {
            mMagnifier = createMagnifier();
        }
        float rowHeight = terminalView.mRenderer.getFontLineSpacing();
        mMagnifier.show(mPointX + mHotspotX, mPointY - rowHeight / 2f);
    }

    private Magnifier createMagnifier() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) {
            //noinspection NewApi
            return new Magnifier(terminalView);
        }
        float density = getResources().getDisplayMetrics().density;
        int rowHeight = terminalView.mRenderer.getFontLineSpacing();
        float zoom = 1.75f;
        //noinspection NewApi
        return new Magnifier.Builder(terminalView)
            .setInitialZoom(zoom)
            .setSize(Math.round(160 * density), Math.round(rowHeight * 3 * zoom))
            .setCornerRadius(12 * density)
            .setDefaultSourceToMagnifierOffset(0, -Math.round(rowHeight * 2 * zoom + mHandleHeight))
            .build();
    }

    private void dismissMagnifier() {
        if (mMagnifier != null && Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            mMagnifier.dismiss();
        }
        // Rebuilt per drag: its size is derived from the font, which pinch-zoom can change.
        mMagnifier = null;
    }

    @Override
    public void onDraw(Canvas c) {
        final int width = mHandleDrawable.getIntrinsicWidth();
        int height = mHandleDrawable.getIntrinsicHeight();
        mHandleDrawable.setBounds(0, 0, width, height);
        mHandleDrawable.draw(c);
    }

    @SuppressLint("ClickableViewAccessibility")
    @Override
    public boolean onTouchEvent(MotionEvent event) {
        terminalView.updateFloatingToolbarVisibility(event);
        switch (event.getActionMasked()) {
            case MotionEvent.ACTION_DOWN: {
                final float rawX = event.getRawX();
                final float rawY = event.getRawY();
                mTouchToWindowOffsetX = rawX - mPointX;
                mTouchToWindowOffsetY = rawY - mPointY;
                final int[] coords = mTempCoords;
                terminalView.getLocationInWindow(coords);
                mLastParentX = coords[0];
                mLastParentY = coords[1];
                mIsDragging = true;
                break;
            }

            case MotionEvent.ACTION_MOVE: {
                mLastRawX = event.getRawX();
                mLastRawY = event.getRawY();
                if (!mAutoscrollPending) dragToLastTouch();
                break;
            }

            case MotionEvent.ACTION_UP:
            case MotionEvent.ACTION_CANCEL:
                mIsDragging = false;
                stopAutoscroll();
                dismissMagnifier();
        }
        return true;
    }

    /**
     * Moves the dragged endpoint under the last touch, then re-arms {@link #mAutoscrollTick} while
     * that move scrolled the view. {@code mTouchToWindowOffsetX} already absorbs a horizontal pan of
     * the host (see {@link #moveTo}), so a still finger lands on the newly revealed columns.
     */
    private void dragToLastTouch() {
        stopAutoscroll();
        if (!mIsDragging) return;
        final float newPosX = mLastRawX - mTouchToWindowOffsetX + mHotspotX;
        final float newPosY = mLastRawY - mTouchToWindowOffsetY + mHotspotY + mTouchOffsetY;

        mCursorController.updatePosition(this, Math.round(newPosX), Math.round(newPosY));
        if (mIsDragging && mCursorController.didAutoscroll()) {
            mAutoscrollPending = true;
            terminalView.postDelayed(mAutoscrollTick, AUTOSCROLL_INTERVAL_MS);
        }
    }

    private void stopAutoscroll() {
        mAutoscrollPending = false;
        terminalView.removeCallbacks(mAutoscrollTick);
    }

    @Override
    public void onMeasure(int widthMeasureSpec, int heightMeasureSpec) {
        setMeasuredDimension(mHandleDrawable.getIntrinsicWidth(),
            mHandleDrawable.getIntrinsicHeight());
    }

    public int getHandleHeight() {
        return mHandleHeight;
    }

    public int getHandleWidth() {
        return mHandleWidth;
    }

    public boolean isShowing() {
        if (mHandle != null)
            return mHandle.isShowing();
        else
            return false;
    }

    public boolean isParentNull() {
        return this.getParent() == null;
    }

    public boolean isDragging() {
        return mIsDragging;
    }

}
