import 'package:flutter/material.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import 'package:video_player/video_player.dart';

const String supabaseUrl = String.fromEnvironment(
  'SUPABASE_URL',
  defaultValue: '',
);

const String supabaseAnonKey = String.fromEnvironment(
  'SUPABASE_ANON_KEY',
  defaultValue: '',
);

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();

  if (supabaseUrl.isEmpty || supabaseAnonKey.isEmpty) {
    runApp(const ConfigErrorApp());
    return;
  }

  await Supabase.initialize(
    url: supabaseUrl,
    anonKey: supabaseAnonKey,
  );

  runApp(const ClipApp());
}

class ConfigErrorApp extends StatelessWidget {
  const ConfigErrorApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      home: Scaffold(
        body: Center(
          child: Padding(
            padding: const EdgeInsets.all(32.0),
            child: Text(
              'Missing Supabase configuration. Rebuild with --dart-define=SUPABASE_URL=... and --dart-define=SUPABASE_ANON_KEY=...',
              textAlign: TextAlign.center,
              style: const TextStyle(fontSize: 18),
            ),
          ),
        ),
      ),
    );
  }
}

class ClipApp extends StatelessWidget {
  const ClipApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Clip Feed',
      theme: ThemeData(
        useMaterial3: true,
        colorSchemeSeed: Colors.deepPurple,
      ),
      home: const ClipsHomePage(),
    );
  }
}

class ClipsHomePage extends StatefulWidget {
  const ClipsHomePage({super.key});

  @override
  State<ClipsHomePage> createState() => _ClipsHomePageState();
}

class _ClipsHomePageState extends State<ClipsHomePage> {
  final TextEditingController _controller = TextEditingController();
  final GlobalKey<FormState> _formKey = GlobalKey<FormState>();
  bool _isSaving = false;

  Future<List<ClipRecord>> _fetchClips() async {
    final response = await Supabase.instance.client
        .from('clips')
        .select()
        .not('public_url', 'is', null)
        .order('created_at', ascending: false);

    return (response as List)
        .map((item) => ClipRecord.fromMap(item))
        .toList();
  }

  Future<void> _saveStreamUrl() async {
    final url = _controller.text.trim();
    if (url.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Paste a YouTube stream URL first.')),
      );
      return;
    }

    setState(() => _isSaving = true);

    try {
      await Supabase.instance.client.from('clips').insert({
        'title': 'New stream request',
        'source_url': url,
        'public_url': null,
        'storage_path': null,
      });

      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Stream URL saved. Waiting for a finished clip.')),
        );
      }

      _controller.clear();
    } catch (e) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Save failed: $e')),
        );
      }
    } finally {
      if (mounted) {
        setState(() => _isSaving = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('My Clips'),
      ),
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(16.0),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Form(
                key: _formKey,
                child: Row(
                  children: [
                    Expanded(
                      child: TextFormField(
                        controller: _controller,
                        decoration: const InputDecoration(
                          labelText: 'YouTube stream URL',
                          border: OutlineInputBorder(),
                        ),
                        validator: (value) {
                          if (value == null || value.trim().isEmpty) {
                            return 'Enter a URL';
                          }
                          return null;
                        },
                      ),
                    ),
                    const SizedBox(width: 12),
                    ElevatedButton(
                      onPressed: _isSaving ? null : _saveStreamUrl,
                      child: _isSaving
                          ? const SizedBox(
                              width: 16,
                              height: 16,
                              child: CircularProgressIndicator(strokeWidth: 2),
                            )
                          : const Text('Save'),
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 18),
              const Text(
                'Finished clips',
                style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold),
              ),
              const SizedBox(height: 12),
              Expanded(
                child: FutureBuilder<List<ClipRecord>>(
                  future: _fetchClips(),
                  builder: (context, snapshot) {
                    if (snapshot.connectionState == ConnectionState.waiting) {
                      return const Center(child: CircularProgressIndicator());
                    }

                    if (snapshot.hasError) {
                      return Center(
                        child: Text('Error loading clips: ${snapshot.error}'),
                      );
                    }

                    final clips = snapshot.data ?? [];
                    if (clips.isEmpty) {
                      return const Center(
                        child: Text('No finished clips yet.'),
                      );
                    }

                    return ListView.builder(
                      itemCount: clips.length,
                      itemBuilder: (context, index) {
                        final clip = clips[index];
                        return ClipCard(clip: clip);
                      },
                    );
                  },
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class ClipCard extends StatelessWidget {
  final ClipRecord clip;

  const ClipCard({super.key, required this.clip});

  @override
  Widget build(BuildContext context) {
    return Card(
      margin: const EdgeInsets.only(bottom: 18),
      child: Padding(
        padding: const EdgeInsets.all(12.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              clip.title,
              style: const TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
            ),
            const SizedBox(height: 8),
            if (clip.publicUrl.isNotEmpty)
              ClipPlayer(publicUrl: clip.publicUrl)
            else
              const Text('Video not ready yet'),
            const SizedBox(height: 8),
            Text(
              'Source: ${clip.sourceUrl}',
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
            ),
          ],
        ),
      ),
    );
  }
}

class ClipPlayer extends StatefulWidget {
  final String publicUrl;

  const ClipPlayer({super.key, required this.publicUrl});

  @override
  State<ClipPlayer> createState() => _ClipPlayerState();
}

class _ClipPlayerState extends State<ClipPlayer> {
  late VideoPlayerController _controller;

  @override
  void initState() {
    super.initState();
    _controller = VideoPlayerController.networkUrl(
      Uri.parse(widget.publicUrl),
    )..initialize().then((_) {
        if (mounted) {
          setState(() {});
        }
      });
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (!_controller.value.isInitialized) {
      return const SizedBox(
        height: 220,
        child: Center(child: CircularProgressIndicator()),
      );
    }

    return AspectRatio(
      aspectRatio: _controller.value.aspectRatio,
      child: VideoPlayer(_controller),
    );
  }
}

class ClipRecord {
  final String id;
  final String title;
  final String sourceUrl;
  final String publicUrl;
  final String storagePath;

  const ClipRecord({
    required this.id,
    required this.title,
    required this.sourceUrl,
    required this.publicUrl,
    required this.storagePath,
  });

  factory ClipRecord.fromMap(Map<String, dynamic> map) {
    return ClipRecord(
      id: map['id']?.toString() ?? '',
      title: map['title']?.toString() ?? 'Clip',
      sourceUrl: map['source_url']?.toString() ?? '',
      publicUrl: map['public_url']?.toString() ?? '',
      storagePath: map['storage_path']?.toString() ?? '',
    );
  }
}
