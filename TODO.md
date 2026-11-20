# مخرجات DocuFlow

> لم تُقدَّم مفاتيح Gemini/Groq/Pixabay/Pollinations حقيقية؛ لذلك لم تُرسل طلبات توليد خارجية. لا يتوفر Docker Engine/Compose في بيئة التطوير الحالية، لذلك لم يُدّعَ بناء صورة Docker أو تشغيل حاوية هنا.

## لوحة الإنتاج وجودة الفيديو
- [x] اختيار موضوع/دفعة، نسبة 16:9 أو 9:16، لغة عربية/إنجليزية وصوت متوافق، ومدة 1/3/5/10 دقائق.
- [x] إنتاج مشاهد سريعة واستعلامات Pixabay دقيقة، ومنع تكرار ID الفيديو في المشروع الواحد.
- [x] تطبيق Ken Burns/crossfade، ترجمة ASS عربية RTL قصيرة، وصوت مضبوط إلى هدف −14 LUFS و−1.0 dBTP.
- [x] اجتاز اختبار الدقيقة العربي: 16 مشهداً، 60.72 ثانية، 26 دفعة ترجمة، ودقة 1280×720؛ قياس EBU بلغ −13.9 LUFS وذروة −1.1 dBFS.

## إصلاح مسارات Pixabay من نوع tmcd
- [x] عند تطبيع/قص كل مصدر مرئي، يستخدم FFmpeg `-map 0:v:0 -an -sn -dn` لإسقاط الصوت والترجمة والبيانات واختيار أول مسار فيديو فقط.
- [x] منع نسخ metadata/folders timecode إلى ملف الإخراج عبر `-map_metadata -1 -map_chapters -1 -write_tmcd 0`.
- [x] اجتاز اختبار رجوع اصطناعي: ملف دخل يحوي video وaudio وsubtitle و`data:tmcd`، ثم صار ملف المشهد المطبع يحوي `video` فقط ومدته المتوقعة.

## Docker والحزمة المحلية
- [x] Dockerfile يتضمن Python 3.12 وFFmpeg/ffprobe/libass والخطوط واعتماديات `requirements.txt` وفحص health؛ يشغل FastAPI كـUID/GID غير root بعد تهيئة volumes.
- [x] `docker-compose.yml` يربط `./data:/app/data` لحفظ الأفلام محلياً، ويحفظ كاش faster-whisper في volume مسمى، ويتيح `docker-compose up -d --build`.
- [x] إضافة `.dockerignore` و`docker-entrypoint.sh`، وتحديث README ومولد الحزمة ليضم ملفات Docker.
- [x] فحص YAML بخادم PyYAML، و`sh -n` للـentrypoint، و`compileall` و`git diff --check`؛ ملفات المصادر التي يشير إليها Dockerfile موجودة.
- [x] تحديث DocuFlow.zip: فحص ضغطه ومحتوياته، مع مشغلات Windows/Linux وملفات Docker وعدم تضمين المفاتيح أو MP4/MP3 أو `.venv`.
- [ ] بناء صورة Docker وتشغيلها فعلياً: غير قابل للاختبار هنا لعدم توفر Docker Engine/Compose؛ يمكن تنفيذ الأمر نفسه محلياً بعد تنزيل الحزمة.
