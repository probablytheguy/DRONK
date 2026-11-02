package org.ouitak.app.core

import java.time.Instant

/** Geometry demo fixtures, not realistic flight performance or captured RF. */
object SyntheticHistory {
    fun observations(now: Instant): List<Detection> {
        val a = listOf(Position(-3.0,-8.0,30.0), Position(0.0,-3.0,35.0), Position(3.0,-5.0,40.0), Position(5.0,0.0,45.0))
        val b = listOf(Position(8.0,20.0,60.0), Position(12.0,24.0,65.0), Position(15.0,20.0,70.0), Position(18.0,26.0,75.0))
        return listOf(a,b).flatMapIndexed { aircraft, positions -> positions.mapIndexed { index, p ->
            val at = now.minusSeconds((positions.lastIndex-index)*5L)
            fun report(position: Position, offset: Long) = ReportedLocation(position, at.minusSeconds(offset),
                LocationProvenance.SIMULATION, LocationValidity.VALID)
            Detection("SIM-DRONE-00${aircraft+1}",at,now,p,ObservationSource.SIMULATION,IdentifierType.SERIAL_NUMBER,
                2.0,if (aircraft==0) 45.0 else 90.0,
                controlStationLocation = report(if (aircraft==0) Position(2.0,-10.0) else Position(10.0,30.0),1),
                takeoffLocation = report(positions.first(),10),
                receiverLocation = ReportedLocation(Position(-5.0,5.0),at.minusSeconds(2),LocationProvenance.SIMULATION,LocationValidity.VALID))
        } }.sortedWith(observationOrder)
    }
}
