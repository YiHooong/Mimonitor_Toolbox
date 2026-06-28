import android.util.Log;
import java.io.File;
import java.io.FileWriter;
import java.lang.reflect.Constructor;
import java.lang.reflect.Method;

public class MtkDirectTool {
    private static final String TAG = "MtkDirectTool";
    private static final String RESULT_FILE = "/data/data/mitv.service/cache/.jni_result";
    private static final String BATCH_RESULT_FILE =
            "/sdcard/Download/Mimonitor_Toolbox/.mtk_batch_result.txt";
    private static final String TV_CLASSPATH =
            "/system/framework/mitvmiddlewareimpl.jar:/system_ext/priv-app/TvServices/TvServices.apk";
    private static final String NATIVE_LIB_PATH = "/vendor/lib64:/system/lib64:/system_ext/lib64";

    private static void log(String msg) {
        System.out.println(msg);
        Log.d(TAG, msg);
    }

    private static void writeFile(String path, String value) {
        try {
            File file = new File(path);
            File parent = file.getParentFile();
            if (parent != null && !parent.exists()) {
                parent.mkdirs();
            }
            FileWriter fw = new FileWriter(file);
            fw.write(value);
            fw.close();
        } catch (Exception e) {
            Log.e(TAG, "Failed to write result file: " + e.getMessage());
        }
    }

    private static void writeResult(String value) {
        writeFile(RESULT_FILE, value);
    }

    private static void writeBatchResult(String value) {
        writeFile(BATCH_RESULT_FILE, value);
    }

    private static ClassLoader tvClassLoader() throws Exception {
        return (ClassLoader) Class.forName("dalvik.system.PathClassLoader")
                .getConstructor(String.class, String.class, ClassLoader.class)
                .newInstance(TV_CLASSPATH, NATIVE_LIB_PATH, ClassLoader.getSystemClassLoader());
    }

    private static void setColorGains(ClassLoader classLoader, int red, int green, int blue) throws Exception {
        Class<?> displayClass = classLoader.loadClass("com.mediatek.twoworlds.factory.MtkTvFApiDisplay");
        Object display = displayClass.getMethod("getInstance").invoke(null);

        Class<?> enumClass = classLoader.loadClass(
                "com.mediatek.twoworlds.factory.common.MtkTvFApiDisplayTypes$EnumColorTemperature");
        Object userColorTemp = Enum.valueOf((Class<Enum>) enumClass.asSubclass(Enum.class),
                "E_MTK_FAPI_COLOR_TEMP_USER");

        Class<?> dataClass = classLoader.loadClass(
                "com.mediatek.twoworlds.factory.common.MtkTvFApiDisplayTypes$ColorTempData");
        Constructor<?> ctor = dataClass.getConstructor(
                int.class, int.class, int.class, int.class, int.class, int.class);
        Object data = ctor.newInstance(red, green, blue, 1024, 1024, 1024);

        Method method = displayClass.getMethod("setWbGainOffsetEx", enumClass, dataClass, int.class);
        int ret = (Integer) method.invoke(display, userColorTemp, data, 0);
        log("RESULT: SET_COLOR_GAINS r=" + red + " g=" + green + " b=" + blue + ", return: " + ret);
        writeResult(String.valueOf(ret));
    }

    private static void setHdrToneMapping(ClassLoader classLoader, Method setConfigValueNative, int value,
            int isUpdate) throws Exception {
        int configRet = (Integer) setConfigValueNative.invoke(
                null, -1, "g_video__vid_hdr_tone_mapping_mode", value, isUpdate);
        Integer hdrAttrRet = null;
        try {
            Class<?> providerClass = classLoader.loadClass("xiaomi.hardware.mitv.provider.V1_0.IMiTVProvider");
            Object provider = providerClass.getMethod("getService", boolean.class).invoke(null, false);
            if (provider != null) {
                hdrAttrRet = (Integer) providerClass.getMethod("setHdrAttr", int.class, int.class)
                        .invoke(provider, 4, value);
            }
        } catch (Exception e) {
            log("WARN: setHdrAttr failed: " + e.getMessage());
        }
        log("RESULT: SET_HDR_TONE_MAPPING value=" + value + ", configReturn: " + configRet
                + ", hdrAttrReturn: " + (hdrAttrRet == null ? "N/A" : hdrAttrRet));
        writeResult(String.valueOf(configRet));
    }

    private static void batchGet(Method getConfigValueNative, String[] keys, int startIndex) {
        StringBuilder sb = new StringBuilder();
        long startMs = System.currentTimeMillis();
        int count = 0;
        for (int i = startIndex; i < keys.length; i++) {
            String k = keys[i];
            if (k == null || k.length() == 0) {
                continue;
            }
            count++;
            try {
                int val = (Integer) getConfigValueNative.invoke(null, -1, k);
                sb.append(k).append("=").append(val).append("\n");
            } catch (Throwable e) {
                sb.append(k).append("=ERROR:").append(e.getClass().getSimpleName()).append("\n");
            }
        }
        sb.append("__elapsed_ms=").append(System.currentTimeMillis() - startMs).append("\n");
        writeBatchResult(sb.toString());
        log("RESULT: BATCH_GET keys=" + count);
    }

    public static void main(String[] args) throws Exception {
        if (args.length == 0) {
            log("Usage: MtkDirectTool <get/set/batchGet/getMinMax/setColorGains/setHdrToneMapping/dump> [args]");
            return;
        }

        String cmd = args[0];
        String key = args.length >= 2 ? args[1] : null;

        try {
            ClassLoader classLoader = tvClassLoader();

            if ("setColorGains".equals(cmd)) {
                if (args.length < 4) {
                    log("ERROR: setColorGains requires red green blue");
                    writeResult("ERROR");
                    return;
                }
                setColorGains(classLoader, Integer.parseInt(args[1]), Integer.parseInt(args[2]),
                        Integer.parseInt(args[3]));
                return;
            }

            Class<?> tvNativeClass = classLoader.loadClass("com.mediatek.twoworlds.tv.TVNative");
            Method getConfigValueNative = tvNativeClass.getDeclaredMethod(
                    "getConfigValue_native", int.class, String.class);
            getConfigValueNative.setAccessible(true);
            Method setConfigValueNative = tvNativeClass.getDeclaredMethod(
                    "setConfigValue_native", int.class, String.class, int.class, int.class);
            setConfigValueNative.setAccessible(true);
            Method getMinMaxNative = tvNativeClass.getDeclaredMethod(
                    "getMinMaxConfigValue_native", int.class, String.class);
            getMinMaxNative.setAccessible(true);

            if ("get".equals(cmd)) {
                if (key == null) {
                    log("ERROR: key is required for get");
                    writeResult("ERROR");
                    return;
                }
                int val = (Integer) getConfigValueNative.invoke(null, -1, key);
                log("RESULT: GET " + key + " = " + val);
                writeResult(String.valueOf(val));

            } else if ("batchGet".equals(cmd)) {
                if (args.length < 2) {
                    log("ERROR: batchGet requires at least one key");
                    writeBatchResult("ERROR\n");
                    return;
                }
                batchGet(getConfigValueNative, args, 1);

            } else if ("setHdrToneMapping".equals(cmd)) {
                if (args.length < 2) {
                    log("ERROR: setHdrToneMapping requires value [isUpdate]");
                    writeResult("ERROR");
                    return;
                }
                int hdrValue = Integer.parseInt(args[1]);
                int hdrIsUpdate = args.length >= 3 ? Integer.parseInt(args[2]) : 3;
                setHdrToneMapping(classLoader, setConfigValueNative, hdrValue, hdrIsUpdate);

            } else if ("set".equals(cmd)) {
                if (key == null) {
                    log("ERROR: key is required for set");
                    writeResult("ERROR");
                    return;
                }
                int value = args.length >= 3 ? Integer.parseInt(args[2]) : 0;
                int isUpdate = args.length >= 4 ? Integer.parseInt(args[3]) : 1;
                int ret = (Integer) setConfigValueNative.invoke(null, -1, key, value, isUpdate);
                log("RESULT: SET " + key + " to " + value + " (isUpdate=" + isUpdate + "), return: " + ret);
                writeResult(String.valueOf(ret));

            } else if ("getMinMax".equals(cmd)) {
                if (key == null) {
                    log("ERROR: key is required for getMinMax");
                    writeResult("ERROR");
                    return;
                }
                int minVal = (Integer) getMinMaxNative.invoke(null, 0, key);
                int maxVal = (Integer) getMinMaxNative.invoke(null, 1, key);
                String result = minVal + "," + maxVal;
                log("RESULT: RANGE " + key + " = [" + minVal + ", " + maxVal + "]");
                writeResult(result);

            } else if ("dump".equals(cmd)) {
                StringBuilder sb = new StringBuilder();
                String[] keys = {
                        "g_disp__disp_back_light",
                        "g_video__brightness",
                        "g_video__contrast",
                        "g_video__vid_sat",
                        "g_video__vid_hue",
                        "g_video__vid_shp",
                        "g_video__vid_local_dimming",
                        "g_video__clr_temp",
                        "g_video__clr_gain_r",
                        "g_video__clr_gain_g",
                        "g_video__clr_gain_b",
                        "g_video__vid_mjc_effect",
                        "g_video__vid_dejudder"
                };
                for (String k : keys) {
                    try {
                        int val = (Integer) getConfigValueNative.invoke(null, -1, k);
                        log(k + ": " + val);
                        sb.append(k).append("=").append(val).append("\n");
                    } catch (Exception e) {
                        log(k + ": ERROR (" + e.getMessage() + ")");
                        sb.append(k).append("=ERROR\n");
                    }
                }
                writeResult(sb.toString());

            } else {
                log("ERROR: Unknown command " + cmd);
                writeResult("ERROR: Unknown command " + cmd);
            }
        } catch (Exception e) {
            log("FATAL ERROR: " + e.getMessage());
            writeResult("FATAL: " + e.getMessage());
            e.printStackTrace();
        }

        System.exit(0);
    }
}
