import 'package:flutter/material.dart';
import 'push.dart';

/// Ask the owner to confirm an in-person garage action. This is deliberately
/// tied to an explicit checkbox and never approves requests in the background.
Future<Map<String, dynamic>> confirmHomePhoneRequest(
  Map<String, dynamic> args,
) async {
  final context = Push.navigatorKey.currentContext;
  if (context == null) {
    return {
      'ok': false,
      'error': 'Open Gajala to confirm this home action in person.',
    };
  }
  final action = args['open'] == true ? 'Open' : 'Close';
  final name = args['name']?.toString() ?? 'the garage door';
  final confirmed = await showOnsiteConfirmationDialog(
    context,
    title: 'Confirm at the garage door',
    detail:
        args['message']?.toString() ??
        '$action $name? Only continue while you are at the door and can observe it.',
  );
  return confirmed
      ? {
          'ok': true,
          'data': {'confirmed': true, 'present': true},
        }
      : {
          'ok': false,
          'cancelled': true,
          'data': {'confirmed': false, 'present': false},
          'error': 'Cancelled; the garage action was not confirmed.',
        };
}

Future<bool> showOnsiteConfirmationDialog(
  BuildContext context, {
  required String title,
  required String detail,
}) async {
  var present = false;
  return await showDialog<bool>(
        context: context,
        barrierDismissible: false,
        builder: (dialogContext) => StatefulBuilder(
          builder: (context, setState) => AlertDialog(
            title: Text(title),
            content: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(detail),
                const SizedBox(height: 12),
                CheckboxListTile(
                  contentPadding: EdgeInsets.zero,
                  controlAffinity: ListTileControlAffinity.leading,
                  value: present,
                  onChanged: (value) => setState(() => present = value == true),
                  title: const Text("I'm at the door and can observe it."),
                ),
              ],
            ),
            actions: [
              TextButton(
                onPressed: () => Navigator.pop(dialogContext, false),
                child: const Text('Cancel'),
              ),
              FilledButton(
                onPressed: present
                    ? () => Navigator.pop(dialogContext, true)
                    : null,
                child: const Text('Confirm'),
              ),
            ],
          ),
        ),
      ) ??
      false;
}
