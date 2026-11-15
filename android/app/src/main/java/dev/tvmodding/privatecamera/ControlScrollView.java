package dev.tvmodding.privatecamera;

import android.content.Context;
import android.util.AttributeSet;
import android.view.View;
import android.widget.ScrollView;

/** Keep a visible camera area when controls grow on small screens or large fonts. */
public final class ControlScrollView extends ScrollView {
    public ControlScrollView(Context context, AttributeSet attributes) { super(context, attributes); }

    @Override protected void onMeasure(int widthSpec, int heightSpec) {
        View root = (View) getParent();
        int available = root.getHeight() - root.getPaddingTop() - root.getPaddingBottom();
        if (available <= 0) available = MeasureSpec.getSize(heightSpec);
        int limit = Math.max(1, Math.round(available * 0.48f));
        super.onMeasure(widthSpec, MeasureSpec.makeMeasureSpec(
            Math.min(limit, MeasureSpec.getSize(heightSpec)), MeasureSpec.AT_MOST));
    }
}
