package cn.gzbib.map;

import android.Manifest;
import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Bundle;
import android.webkit.GeolocationPermissions;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import androidx.webkit.WebViewAssetLoader;

public class MainActivity extends Activity {
    private static final String HOME_HOST = "appassets.androidplatform.net";
    private static final String SRC = "andr.gzbib.map";
    private static final int REQ_LOCATION = 7;

    private WebView web;
    private GeolocationPermissions.Callback pendingGeoCallback;
    private String pendingGeoOrigin;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        web = new WebView(this);
        setContentView(web);

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setGeolocationEnabled(true);
        s.setAllowFileAccess(false);
        s.setAllowContentAccess(false);

        final WebViewAssetLoader loader = new WebViewAssetLoader.Builder()
                .addPathHandler("/assets/", new WebViewAssetLoader.AssetsPathHandler(this))
                .build();

        web.setWebViewClient(new WebViewClient() {
            @Override
            public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
                return loader.shouldInterceptRequest(request.getUrl());
            }

            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                return handleNavigation(request.getUrl());
            }
        });

        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onGeolocationPermissionsShowPrompt(String origin, GeolocationPermissions.Callback callback) {
                if (hasLocationPermission()) {
                    callback.invoke(origin, true, false);
                } else {
                    pendingGeoOrigin = origin;
                    pendingGeoCallback = callback;
                    requestPermissions(new String[]{
                            Manifest.permission.ACCESS_FINE_LOCATION,
                            Manifest.permission.ACCESS_COARSE_LOCATION}, REQ_LOCATION);
                }
            }
        });

        if (savedInstanceState != null) {
            web.restoreState(savedInstanceState);
        } else {
            StringBuilder url = new StringBuilder("https://" + HOME_HOST + "/assets/index.html?repo=")
                    .append(Uri.encode(BuildConfig.REPO));
            if (!BuildConfig.BAIDU_AK.isEmpty()) url.append("&ak=").append(Uri.encode(BuildConfig.BAIDU_AK));
            web.loadUrl(url.toString());
        }
    }

    private boolean hasLocationPermission() {
        return checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED
                || checkSelfPermission(Manifest.permission.ACCESS_COARSE_LOCATION) == PackageManager.PERMISSION_GRANTED;
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == REQ_LOCATION && pendingGeoCallback != null) {
            pendingGeoCallback.invoke(pendingGeoOrigin, hasLocationPermission(), false);
            pendingGeoCallback = null;
            pendingGeoOrigin = null;
        }
    }

    /** App 内页面留在 WebView，其余链接交给百度地图 App、拨号或系统浏览器。 */
    private boolean handleNavigation(Uri uri) {
        String scheme = uri.getScheme() == null ? "" : uri.getScheme();
        String host = uri.getHost() == null ? "" : uri.getHost();
        String path = uri.getPath() == null ? "" : uri.getPath();

        if (HOME_HOST.equals(host)) return false;

        if ("tel".equals(scheme)) {
            open(new Intent(Intent.ACTION_DIAL, uri), null);
            return true;
        }

        if ("api.map.baidu.com".equals(host) && path.startsWith("/marker")) {
            Uri app = new Uri.Builder().scheme("baidumap").authority("map").path("/marker")
                    .appendQueryParameter("location", nz(uri.getQueryParameter("location")))
                    .appendQueryParameter("title", nz(uri.getQueryParameter("title")))
                    .appendQueryParameter("content", nz(uri.getQueryParameter("content")))
                    .appendQueryParameter("coord_type", "bd09ll")
                    .appendQueryParameter("src", SRC).build();
            open(new Intent(Intent.ACTION_VIEW, app), uri);
            return true;
        }

        if ("map.baidu.com".equals(host) && path.startsWith("/search")) {
            String wd = uri.getQueryParameter("wd");
            if (wd != null) {
                Uri app = new Uri.Builder().scheme("baidumap").authority("map").path("/place/search")
                        .appendQueryParameter("query", wd)
                        .appendQueryParameter("region", "广州")
                        .appendQueryParameter("src", SRC).build();
                open(new Intent(Intent.ACTION_VIEW, app), uri);
                return true;
            }
        }

        open(new Intent(Intent.ACTION_VIEW, uri), null);
        return true;
    }

    private void open(Intent intent, Uri fallback) {
        try {
            startActivity(intent);
        } catch (ActivityNotFoundException e) {
            if (fallback != null) {
                try { startActivity(new Intent(Intent.ACTION_VIEW, fallback)); } catch (ActivityNotFoundException ignored) { }
            }
        }
    }

    private static String nz(String s) { return s == null ? "" : s; }

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        super.onSaveInstanceState(outState);
        web.saveState(outState);
    }

    @Override
    public void onBackPressed() {
        if (web.canGoBack()) web.goBack();
        else super.onBackPressed();
    }
}
