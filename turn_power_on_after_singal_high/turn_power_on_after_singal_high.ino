#include <JeeLib.h>

ISR(WDT_vect) { Sleepy::watchdogEvent(); }
/*
 this unit will apply power to the raspberrypi and will turn the
 power off when it reads a signal to be low.
 
 */
int flag =0; // buffer for RPI BUFFER
int time_sleep = 20*1000; //20 seconds
int time_wait_to_turn_off = 60*1000; //60seconds wait before power down
int time_interval_checking = 1*1000; // 1 sec interval of waiting for signal
// the setup function runs once when you press reset or power the board
void setup() {
  // initialize digital pin 13 as an output.
  pinMode(4, INPUT); //read signal from RPI
  pinMode(5, OUTPUT); //sleeping mode
  pinMode(6, OUTPUT); //led, powering down timer
  pinMode(7, OUTPUT); //led, power of RPI
  pinMode(8, OUTPUT); //to NPN to raspberry
}

// the loop function runs over and over again forever
void loop() {
  Sleepy::loseSomeTime(1000); //wait for 1 second
  //AWAKE
  digitalWrite(8, HIGH); //to NPN to raspberry
  digitalWrite(7,HIGH); //led, power of RPI
  digitalWrite(5,LOW); //pin 5 is sleeping mode
  Sleepy::loseSomeTime(10000); // wait for 10 second 
  flag =0;
  while (digitalRead(4) ==LOW || flag<4){
    Sleepy::loseSomeTime(time_interval_checking);
    if(digitalRead(4)==HIGH){flag=flag+1;}
  }
  //confirm can sleep, wait a buffer time then sleep.
  // WAITING TO SLEEP
  digitalWrite(6, HIGH); //led, powering down timer
  Sleepy::loseSomeTime(time_wait_to_turn_off);
  digitalWrite(6, LOW);
  if(digitalRead(4)==HIGH){
  digitalWrite(6, HIGH);
  Sleepy::loseSomeTime(time_wait_to_turn_off);
  digitalWrite(6, LOW);
  }
  //before pwoering down sequence (blink twice)
  digitalWrite(6, HIGH);
  Sleepy::loseSomeTime(800);
  digitalWrite(6, LOW);
  Sleepy::loseSomeTime(800);
  digitalWrite(6, HIGH);
  Sleepy::loseSomeTime(800);
  digitalWrite(6, LOW);
  Sleepy::loseSomeTime(800);
  //SLEEP & WAIT
    digitalWrite(8, LOW); //to NPN to raspberry
    digitalWrite(7, LOW); //led, power of RPI
    digitalWrite(5,HIGH); //pin 5 is sleeping mode
  Sleepy::loseSomeTime(800);
  digitalWrite(5, LOW);
  Sleepy::loseSomeTime(800);
  digitalWrite(5, HIGH);
  Sleepy::loseSomeTime(800);
  digitalWrite(5, LOW);
    Sleepy::loseSomeTime(time_sleep);
}
