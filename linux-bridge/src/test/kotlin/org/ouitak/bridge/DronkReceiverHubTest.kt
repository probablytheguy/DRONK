package org.ouitak.bridge

import org.junit.Assert.*
import org.junit.Test
import org.ouitak.app.core.*
import org.ouitak.app.core.serial.*
import java.time.Clock
import java.time.Instant
import java.time.ZoneId
import java.time.ZoneOffset

class DronkReceiverHubTest {
    private class Time(var now: Instant=Instant.parse("2026-10-28T20:00:00Z")):Clock(){
        override fun instant()=now
        override fun getZone():ZoneId=ZoneOffset.UTC
        override fun withZone(zone:ZoneId):Clock=this
    }
    private val fixture=javaClass.getResource("/dronk/observation.json")!!.readText()
    private val out=mutableListOf<ProcessedSerialDetection>()
    private val time=Time()
    private fun hub(expected:Map<String,String?> = mapOf("a" to null,"b" to null))=DronkReceiverHub(expected,clock=time,onProcessed=out::add).also { expected.keys.forEach(it::connected) }
    private fun feed(h:DronkReceiverHub,s:String="a",line:String=fixture)=h.feed(s,line.toByteArray(),time.now)

    @Test fun optionalResetReasonAndUptimeSurviveDiagnosticsWithoutChangingIdentity(){
        val h=hub()
        feed(h,line=fixture.replace("\"version\":1", "\"reset_reason\":6,\"version\":1"))
        assertEquals(6,h.snapshots()[0].resetReason)
        assertTrue(h.snapshots()[0].uptimeUs!!>0)
        assertEquals(1,out.size)
        val old=hub(mapOf("a" to null))
        feed(old)
        assertNull(old.snapshots()[0].resetReason)
    }
    @Test fun watchdogSessionStatusAfterReconnectPreservesSplitCountersWithoutAircraft(){
        val h=hub();feed(h)
        h.feed("a","{partial".toByteArray(),time.now)
        h.disconnected("a");h.connected("a")
        val status=javaClass.getResource("/dronk/status-preflight.json")!!.readText()
        feed(h,line=status)
        val snapshot=h.snapshots()[0]
        assertEquals("b".repeat(32),snapshot.sessionId)
        assertEquals(6,snapshot.resetReason)
        assertEquals(5000000L,snapshot.uptimeUs)
        for((key,value) in mapOf("malformed_frames" to 3uL,"unsupported_versions" to 4uL,
                "decode_failures" to 4uL,"queue_drops" to 9uL,"usb_output_drops" to 12uL))
            assertEquals(value,snapshot.firmwareCounters[key])
        assertEquals(1,out.size) // status cannot emit a detection or CoT
        feed(h) // retired pre-reset session must not return
        assertEquals(1,out.size)
        assertEquals(1,h.snapshots()[0].rejected)
    }
    @Test fun publicAliasesHideRawFactoryIdsWithoutChangingAssociationOrTracks(){
        val raw="c5-021122334455" // synthetic locally administered test identity
        val h=DronkReceiverHub(mapOf("front" to raw),clock=time,receiverAliases=mapOf("front" to "north"),onProcessed=out::add)
        h.connected("front")
        h.feed("front",fixture.replace("c5-fixture-a",raw).toByteArray(),time.now)
        val result=out.single()
        assertEquals(raw,result.detection.dronk!!.receiverId) // internal association retained
        assertEquals(raw,h.snapshots()[0].receiverId)
        assertTrue(result.cotXml!!.contains("receiverId=\"north\""))
        assertFalse(result.cotXml!!.contains(raw))
        assertFalse(DronkJson.encode(dronkAircraft(result.detection)).contains(raw))
        val uid=result.detection.cotUid
        val plain=hub();feed(plain)
        assertEquals(uid,out.last().detection.cotUid)
        assertFalse(out.last().cotXml!!.contains("c5-fixture-a")) // stream label is safe default
    }
    @Test fun fixtureNormalizesTypedIdentityAndPreservesMetadataAndRoles(){
        val h=hub();feed(h)
        val d=out.single().detection
        assertEquals(IdentifierType.SERIAL_NUMBER,d.identifierType)
        assertEquals(ObservationSource.DRONK_C5_USB,d.source)
        assertEquals(time.now.minusSeconds(1),d.observedAt)
        assertEquals(38.0,d.position!!.latitude,0.0)
        assertNull(d.controlStationLocation)
        assertEquals(38.1,d.takeoffLocation!!.position!!.latitude,.00001)
        assertEquals(5745,d.dronk!!.frequencyMhz)
        assertTrue(out.single().cotXml!!.contains("DRONK C5 USB"))
        assertTrue(out.single().cotXml!!.contains("operatorLocationType=\"0\""))
    }
    @Test fun eachSplitPointAndIndependentFraming(){
        for(split in fixture.indices){
            out.clear();val h=hub();val bytes=fixture.toByteArray()
            h.feed("a",bytes.copyOfRange(0,split),time.now)
            feed(h,"b",fixture.replace("c5-fixture-a","c5-fixture-b").replace("SYNTH-C5-TEST","SECOND"))
            h.feed("a",bytes.copyOfRange(split,bytes.size),time.now)
            assertEquals(2,out.size)
        }
    }
    @Test fun disconnectDropsPartialButRetainsReplayProtection(){
        val h=hub();h.feed("a",fixture.take(50).toByteArray(),time.now);h.disconnected("a");h.connected("a");feed(h)
        assertEquals(1,out.size);h.disconnected("a");h.connected("a");feed(h)
        assertEquals(1,out.size);assertEquals(1,h.snapshots()[0].duplicates)
    }
    @Test fun receiverIdentityNeverBecomesAircraftIdentityAndCrossReceiverDuplicatesDoNotRefresh(){
        val h=hub();feed(h);feed(h,"b",fixture.replace("c5-fixture-a","c5-fixture-b"))
        assertEquals(1,out.count { it.tracking is TrackUpdate.Updated });assertEquals(1,h.activeTracks().size)
        assertEquals("c5-fixture-b",out.last().detection.dronk!!.receiverId)
        assertNull(out.last().cotXml)
        assertEquals(1,h.snapshots()[1].duplicates)
    }
    @Test fun groundLocationsAreSeparateMarkersOnlyWhenBroadcastAndForwarded(){
        val h=hub();feed(h)
        val d=out.single().detection
        val cot=CotGenerator()
        assertEquals(emptyList<String>(),cot.generateGroundLocationsForForwarding(d,false))
        val takeoff=cot.generateGroundLocationsForForwarding(d,true).single()  // fixture: operator location type 0 = take-off
        assertTrue(takeoff.contains("uid=\"${d.cotUid}-takeoff\" type=\"b-m-p-s-m\""))  // a fixed point: map spot
        assertTrue(takeoff.contains("lat=\"38.1\""))
        assertTrue(takeoff.contains("<link uid=\"${d.cotUid}\" type=\"a-u-A-M-H-Q\" relation=\"p-p\"/>"))
        assertTrue(cot.generateForForwarding(d,true).contains("lat=\"38.0\""))  // the aircraft event keeps the aircraft position
        val live=d.copy(controlStationLocation=d.takeoffLocation,takeoffLocation=null)  // type 1/2: operator
        assertTrue(cot.generateGroundLocationsForForwarding(live,true).single().contains("uid=\"${d.cotUid}-operator\""))
        val none=d.copy(takeoffLocation=ReportedLocation(null,d.observedAt,LocationProvenance.REMOTE_ID_BROADCAST,LocationValidity.UNAVAILABLE))
        assertEquals(emptyList<String>(),cot.generateGroundLocationsForForwarding(none,true))  // never invented
        val zero=d.copy(takeoffLocation=ReportedLocation(Position(0.0,0.0,null),d.observedAt,LocationProvenance.REMOTE_ID_BROADCAST,LocationValidity.VALID))
        assertEquals(emptyList<String>(),cot.generateGroundLocationsForForwarding(zero,true))
    }
    @Test fun missingAndInvalidAircraftNeverBorrowOperatorPosition(){
        for(bad in listOf("null","0.0","100.0")){
            val h=hub();feed(h,line=fixture.replace("\"latitude\":38.0000000","\"latitude\":$bad").replace("\"latitude\":38.0","\"latitude\":$bad"))
            // zero latitude alone is valid, zero pair is tested below.
            if(bad!="0.0") assertTrue(out.isEmpty())
            out.clear()
        }
        val h=hub();feed(h,line=fixture.replace("38.0","0.0").replace("-90.0","0.0"));assertTrue(out.isEmpty())
    }
    @Test fun malformedUnknownVersionAndBadMetadataRejected(){
        for(line in listOf("{bad}\n",fixture.replace("\"version\":1","\"version\":2"),fixture.replace("5745","2437"),
            fixture.replace("\"uptime_us\":2000000","\"uptime_us\":-1"),fixture.replace("\"rssi_dbm\":-60","\"rssi_dbm\":20"))){
            val h=hub();feed(h,line=line);assertTrue(out.isEmpty());assertEquals(1,h.snapshots()[0].rejected)
        }
    }
    @Test fun statusErrorAndHelloNeverCreateTracks(){
        val base="""{"protocol":"ouitak.receiver","version":1,"receiver_id":"c5-a","session_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","seq":0,"uptime_us":0,"receiver_utc":null,"type":"error","operation":"tune","code":-1}"""+"\n"
        val h=hub();feed(h,line=base);assertTrue(out.isEmpty());assertEquals("receiver_error",h.snapshots()[0].state)
    }
    @Test fun statusPreservesFirmwareCountersAndStaleAircraftExpire(){
        val h=hub();feed(h)
        val status=dronkDemo(10).toMutableMap();status["receiver_id"]="c5-fixture-a";status["session_id"]="a".repeat(32);status["type"]="status";status["state"]="monitoring";status["counters"]=mapOf("rf_frames" to 100,"queue_drops" to 4)
        feed(h,line=DronkJson.encode(status)+"\n");assertEquals(1,out.size);assertEquals(4uL,h.snapshots()[0].firmwareCounters["queue_drops"])
        time.now=time.now.plusSeconds(61);assertTrue(h.activeTracks().isEmpty())
    }
    @Test fun expectedIdAndCollisionAreRejected(){
        val h=hub(mapOf("a" to "different","b" to null));feed(h);assertTrue(out.isEmpty())
        val h2=hub();feed(h2);feed(h2,"b");assertEquals(1,out.size);assertEquals(1,h2.snapshots()[1].rejected)
    }
    @Test fun newSessionRequiresHelloAndRetiredSessionCannotReturn(){
        val h=hub();feed(h)
        val hello=dronkDemo(0).toMutableMap();hello["receiver_id"]="c5-fixture-a";hello["session_id"]="b".repeat(32);hello["type"]="hello"
        hello["firmware"]="C5-0.1";hello["board"]="seeed_xiao_esp32c5";hello["decoder_revision"]="fixture";hello["passive"]=true;hello["hopping"]=false;hello["ble"]=false
        feed(h,line=DronkJson.encode(hello)+"\n");feed(h)
        assertEquals(1,out.size);assertEquals(1,h.snapshots()[0].rejected)
    }
    @Test fun legacyRecordStillUsesProvisionalPolicy(){
        val h=hub();feed(h,line="""{"mac":"02:11:22:33:44:55","rssi":-61,"drone_lat":38.627,"drone_long":-90.1994,"basic_id":"LEGACY"}"""+"\n")
        assertEquals(IdentifierType.PROVISIONAL_UNTYPED,out.single().detection.identifierType)
        assertTrue(out.single().cotXml!!.contains("PROVISIONAL OUI-SPY USB"))
    }
    private fun scanStatus(seq:Long, channel:Int, scan:Map<String,Any?>?=dronkDemoScan(5).toMutableMap().also { it["current"]=channel }):String{
        val status=dronkDemo(seq).toMutableMap();status["receiver_id"]="c5-fixture-a";status["session_id"]="a".repeat(32);status["type"]="status"
        status["state"]="monitoring";status["counters"]=mapOf("rf_frames" to 100);status["channel"]=channel
        status["frequency_mhz"]=if(channel<=11) 2407+channel*5 else 5000+channel*5
        if(scan!=null) status["scan"]=scan
        return DronkJson.encode(status)+"\n"
    }
    @Test fun hoppingHelloAndScanTelemetryAcrossChannels(){
        val h=hub()
        val hello=dronkDemo(0).toMutableMap();hello["receiver_id"]="c5-fixture-a";hello["session_id"]="a".repeat(32);hello["type"]="hello"
        hello["firmware"]="C5-0.3-scan";hello["board"]="seeed_xiao_esp32c5";hello["decoder_revision"]="fixture";hello["passive"]=true;hello["hopping"]=true;hello["ble"]=false
        feed(h,line=DronkJson.encode(hello)+"\n")
        feed(h,line=scanStatus(1,6));feed(h,line=scanStatus(2,36));feed(h,line=scanStatus(3,149))
        val snapshot=h.snapshots()[0]
        assertEquals(0,snapshot.rejected)
        assertEquals(149,snapshot.channel)
        val scan=snapshot.scan!!
        assertEquals("hop",scan.mode);assertEquals(149,scan.current);assertEquals(20,scan.channels.size)
        assertEquals(listOf(149),scan.channels.filter{it.hot}.map{it.channel})
        assertNull(scan.channels.first{it.channel==6}.lastDecodeAgeS)
        feed(h,line=dronkDemo(4).toMutableMap().also{it["receiver_id"]="c5-fixture-a";it["session_id"]="a".repeat(32);it["channel"]=36;it["frequency_mhz"]=5180}.let{DronkJson.encode(it)+"\n"})
        assertEquals(1,out.size);assertEquals(36,out.single().detection.dronk!!.channel)
        feed(h,line=scanStatus(5,6,scan=null))
        assertNull(h.snapshots()[0].scan) // a status without scan clears stale scan telemetry
    }
    @Test fun invalidScanTelemetryIsRejected(){
        fun mutate(change:(MutableMap<String,Any?>)->Unit)=dronkDemoScan(5).toMutableMap().also{it["current"]=6;change(it)}
        @Suppress("UNCHECKED_CAST") fun rows(m:MutableMap<String,Any?>)=(m["channels"] as List<List<Any?>>).map{it.toMutableList()}.toMutableList()
        val bad=listOf(
            mutate{it["mode"]="sweep"}, mutate{it["current"]=149}, mutate{it["dwell_ms"]=10}, mutate{it["switches"]=4294967296L},
            mutate{it["on_channel_ms"]=null}, mutate{it["channels"]=emptyList<Any>()},
            mutate{m->m["channels"]=rows(m).also{it[3][0]=52}}, mutate{m->m["channels"]=rows(m).also{it[3][0]=6}},
            mutate{m->m["channels"]=rows(m).also{it[3].removeAt(6)}}, mutate{m->m["channels"]=rows(m).also{it[3][6]=2}},
            mutate{m->m["channels"]=rows(m).also{it[3][2]=-1}}, mutate{m->m["mode"]="fixed"})
        for((i,scan) in bad.withIndex()){
            val h=hub();feed(h,line=scanStatus(1,6,scan))
            assertEquals("case $i",1,h.snapshots()[0].rejected);assertNull(h.snapshots()[0].scan)
        }
        val fixed=dronkDemoScan(5).toMutableMap().also{it["mode"]="fixed";it["current"]=6;it["channels"]=listOf(listOf(6,10,1,0,0,null,0))}
        val h=hub();feed(h,line=scanStatus(1,6,fixed))
        assertEquals(0,h.snapshots()[0].rejected);assertEquals("fixed",h.snapshots()[0].scan!!.mode)
    }
    private val receptionFixture=javaClass.getResource("/dronk/reception.json")!!.readText()
    private fun receptionHub(into:MutableList<DronkRidReception>)=DronkReceiverHub(mapOf("a" to null),clock=time,onProcessed=out::add,onReception={into.add(it)}).also{it.connected("a")}
    @Test fun validUntrackableRemoteIdIsReportedButNeverTracked(){
        val got=mutableListOf<DronkRidReception>();val h=receptionHub(got)
        h.feed("a",receptionFixture.toByteArray(),time.now)
        val r=got.single()
        assertEquals(listOf("basic_id","location","system"),r.messages)
        assertEquals(listOf(DronkBasicId(1,2,"SYNTH-C5-TEST")),r.basicIds)
        assertEquals(Triple("no_valid_position","unavailable",149),Triple(r.reason,r.aircraftLocation,r.channel))
        assertEquals(time.now.minusSeconds(1),r.observedAt);assertFalse(r.synthetic)
        assertTrue(out.isEmpty());assertTrue(h.activeTracks().isEmpty())
        val snap=h.snapshots()[0];assertEquals(0,snap.rejected);assertEquals("receiving",snap.state);assertEquals(0,snap.accepted)
        h.feed("a",receptionFixture.toByteArray(),time.now) // same seq: duplicate, not a second reception
        assertEquals(1,got.size);assertEquals(1,h.snapshots()[0].duplicates)
    }
    @Test fun receptionEventCarriesReceiverProvenance(){
        val got=mutableListOf<DronkRidReception>();val h=receptionHub(got)
        h.feed("a",receptionFixture.toByteArray(),time.now)
        val e=dronkReception(got.single())
        assertEquals(listOf("a".repeat(32),"4",7,"02:00:00:00:00:01"),listOf(e["session_id"],e["seq"],e["message_counter"],e["transmitter"]))
        assertEquals(listOf("no_valid_position","unavailable",false),listOf(e["reason"],e["aircraft_location"],e["synthetic"]))
        assertTrue(e["receiver_id"] is String)
    }
    @Test fun inconsistentOrUnknownReceptionContentIsRejected(){
        // Mirrors /dronk/reception.json (asserted equal below) so single fields can be mutated.
        val base=mapOf<String,Any?>("protocol" to "ouitak.receiver","version" to 1,"type" to "reception","receiver_id" to "c5-fixture-a",
            "session_id" to "a".repeat(32),"seq" to 4,"uptime_us" to 2000000,"receiver_utc" to null,"reset_reason" to 0,
            "odid_protocol_version" to 2,"received_uptime_us" to 1000000,"transport" to "wifi_beacon","channel" to 149,
            "frequency_mhz" to 5745,"rssi_dbm" to -60,"transmitter" to "02:00:00:00:00:01","message_counter" to 7,
            "decoded_messages" to 3,"messages" to listOf("basic_id","location","system"),
            "basic_ids" to listOf(mapOf("id_type" to 1,"ua_type" to 2,"value" to "SYNTH-C5-TEST")),
            "aircraft_location" to "unavailable","trackable" to false,"reason" to "no_valid_position","authenticated" to false)
        assertEquals(receptionFixture.trim(),DronkJson.encode(base))
        fun with(change:(MutableMap<String,Any?>)->Unit)=DronkJson.encode(base.toMutableMap().also(change))+"\n"
        fun id(type:Int,value:String?)=mapOf("id_type" to type,"ua_type" to 2,"value" to value)
        val cases=listOf(
            with{it["trackable"]=true}, with{it["authenticated"]=true}, with{it["transport"]="ble"}, with{it["channel"]=52;it["frequency_mhz"]=5260},
            with{it["messages"]=listOf("basic_id","foo")}, with{it["messages"]=listOf("location","basic_id","system")},
            with{it["messages"]=listOf("basic_id","basic_id","location")}, with{it["messages"]=emptyList<String>()},
            with{it["basic_ids"]=listOf(id(1,"A"),id(2,"B"),id(1,"C"))}, with{it["basic_ids"]=listOf(id(3,"UUID-AS-TEXT"))},
            with{it["basic_ids"]=listOf(id(1,"BAD\u0001ID"))}, with{it["basic_ids"]=listOf(id(1," PAD"))},
            with{it["basic_ids"]=listOf(mapOf("id_type" to 1,"ua_type" to 2,"value" to "X","extra" to 1))},
            with{it["reason"]="no_textual_id"}, with{it["reason"]="unknown"}, with{it["aircraft_location"]="valid"},
            with{it["messages"]=listOf("basic_id","system");it["aircraft_location"]="invalid";it["decoded_messages"]=2},
            with{it["basic_ids"]=emptyList<Any>()}, with{it["decoded_messages"]=0}, with{it["decoded_messages"]=2},
            with{it["received_uptime_us"]=3000000})
        for((i,line) in cases.withIndex()){
            val got=mutableListOf<DronkRidReception>();val h=receptionHub(got)
            h.feed("a",line.toByteArray(),time.now)
            assertTrue("case $i",got.isEmpty());assertEquals("case $i",1,h.snapshots()[0].rejected)
        }
        val got=mutableListOf<DronkRidReception>();val h=receptionHub(got)
        h.feed("a",with{it["messages"]=listOf("location","system");it["basic_ids"]=emptyList<Any>();it["reason"]="no_basic_id";it["aircraft_location"]="valid"}.toByteArray(),time.now)
        assertEquals("no_basic_id",got.single().reason)
    }
    @Test fun oversizedAndInvalidUtf8Recover(){
        val h=hub();h.feed("a",ByteArray(5000){65}+byteArrayOf(10),time.now);h.feed("a",byteArrayOf(-1,10),time.now);feed(h)
        assertEquals(1,out.size);assertEquals(2,h.snapshots()[0].rejected)
    }
}
