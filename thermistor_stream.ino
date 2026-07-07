/*
  thermistor_stream.ino
  Streams tick count + raw analog readings from A0, A1, A2 over Serial as CSV.

  Output format:
  tick_ms,a0,a1,a2
  1234,512,498,501
*/

const unsigned long BAUD_RATE = 115200;
const unsigned long SAMPLE_PERIOD_MS = 100;  // 10 Hz

unsigned long lastSampleMs = 0;

void setup() {
  Serial.begin(BAUD_RATE);
  while (!Serial) {
    ;
  }

  analogReference(DEFAULT); // Uses board Vcc (typically 5V on many Arduino boards)

  Serial.println("tick_ms,a0,a1,a2");
}

void loop() {
  unsigned long now = millis();

  if (now - lastSampleMs >= SAMPLE_PERIOD_MS) {
    lastSampleMs = now;

    int a0 = analogRead(A0);
    int a1 = analogRead(A1);
    int a2 = analogRead(A2);

    Serial.print(now);
    Serial.print(',');
    Serial.print(a0);
    Serial.print(',');
    Serial.print(a1);
    Serial.print(',');
    Serial.println(a2);
  }
}
