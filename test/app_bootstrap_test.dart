import 'package:flutter_test/flutter_test.dart';
import 'package:sn_hospital_monitor/app.dart';

void main() {
  testWidgets('app boots with query and settings tabs', (tester) async {
    await tester.pumpWidget(const HospitalMonitorApp());

    expect(find.text('查询'), findsOneWidget);
    expect(find.text('设置'), findsOneWidget);
  });
}
