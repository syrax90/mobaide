# kotlinx.serialization
-keepattributes *Annotation*, InnerClasses
-dontnote kotlinx.serialization.AnnotationsKt

-keepclassmembers class kotlinx.serialization.json.** {
    *** Companion;
}
-keepclasseswithmembers class kotlinx.serialization.json.** {
    kotlinx.serialization.KSerializer serializer(...);
}

-keep,includedescriptorclasses class com.mobaide.app.**$$serializer { *; }
-keepclassmembers class com.mobaide.app.** {
    *** Companion;
}
-keepclasseswithmembers class com.mobaide.app.** {
    kotlinx.serialization.KSerializer serializer(...);
}

# OkHttp
-dontwarn okhttp3.internal.platform.**
-dontwarn org.conscrypt.**
-dontwarn org.bouncycastle.**
-dontwarn org.openjsse.**

# review-ktx references this annotation, which no dependency on the classpath ships
-dontwarn com.google.android.gms.common.annotation.NoNullnessRewrite
